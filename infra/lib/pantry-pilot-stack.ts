import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as route53 from 'aws-cdk-lib/aws-route53';
import * as route53Targets from 'aws-cdk-lib/aws-route53-targets';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as ses from 'aws-cdk-lib/aws-ses';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import { Construct } from 'constructs';

export interface PantryPilotStackProps extends cdk.StackProps {
  /** Shared secret CloudFront adds to every API request; the Lambda rejects calls without it. */
  originSecret: string;
  /** "bedrock" for live Amazon Nova, "mock" while Bedrock quota is unavailable. */
  novaMode: 'bedrock' | 'mock';
  /** Temporary: a role in another account to call Nova through while this account's quota is 0. */
  novaRoleArn?: string;
  /**
   * Our own domain, with a Route 53 hosted zone in this account. When set, the app is served at
   * https://<domain> and the domain is verified for sending email with Amazon SES.
   */
  domainName?: string;
  /**
   * Email-code sign-in (Cognito custom authentication). Codes are sent from no-reply@<domain> by our
   * own Lambda through SES in the account owning `sesRoleArn`: a role there that allows
   * ses:SendEmail for the domain and trusts the role named OTP_SENDER_ROLE_NAME in this account.
   * Used because this account's SES is still in the sandbox.
   */
  sesRoleArn?: string;
  /** Which Instacart Developer Platform keys to use: "development" (default) or "production". */
  instacartEnv?: 'development' | 'production';
}

/** SSM SecureString holding the Instacart API key; added by hand so the key never enters the template. */
export const INSTACART_KEY_PARAM = '/pantry-pilot/instacart-api-key';
/** SSM SecureString with the Kroger app's {"clientId", "clientSecret"}; also added by hand. */
export const KROGER_CREDENTIALS_PARAM = '/pantry-pilot/kroger';

/** Fixed so the SES account's role can trust it by name. */
export const OTP_SENDER_ROLE_NAME = 'pantry-pilot-otp-sender';

// AWS Lambda Web Adapter 1.1.0 (arm64): runs our Starlette app unchanged and streams responses.
const WEB_ADAPTER_LAYER_VERSION = 30;

export class PantryPilotStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: PantryPilotStackProps) {
    super(scope, id, props);

    // One item per household: { pk, doc } where doc is the pantry as JSON.
    const table = new dynamodb.TableV2(this, 'Households', {
      partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING },
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const domainName = props.domainName;
    const emailCodes = Boolean(domainName && props.sesRoleArn);
    const zone = domainName ? route53.HostedZone.fromLookup(this, 'Zone', { domainName }) : undefined;
    if (zone) {
      // Lets SES send as no-reply@<domain>; DKIM and MAIL FROM records go into the hosted zone.
      new ses.EmailIdentity(this, 'EmailDomain', {
        identity: ses.Identity.publicHostedZone(zone),
        mailFromDomain: `mail.${domainName}`,
      });
      // DMARC (monitor only) helps mailbox providers trust mail from the domain.
      new route53.TxtRecord(this, 'Dmarc', { zone, recordName: '_dmarc', values: ['v=DMARC1; p=none;'] });
    }

    // Cognito: sign-in for the web app, and the OAuth provider Alexa+ uses to link accounts.
    // With email codes on, accounts are created by our server on first sign-in (see /api/auth/start),
    // never by public sign-up: otherwise anyone could pre-register someone else's email with a password.
    const users = new cognito.UserPool(this, 'Users', {
      userPoolName: 'pantry-pilot-users',
      featurePlan: cognito.FeaturePlan.ESSENTIALS,
      selfSignUpEnabled: !emailCodes,
      signInAliases: { email: true },
      autoVerify: { email: true },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // Cognito triggers (Node.js, see lambda/auth): branded emails, and the email-code challenge.
    const authCode = lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'auth'));
    const trigger = (id: string, handler: string, extra: Partial<lambda.FunctionProps> = {}) =>
      new lambda.Function(this, id, {
        runtime: lambda.Runtime.NODEJS_22_X,
        architecture: lambda.Architecture.ARM_64,
        code: authCode,
        handler,
        memorySize: 256,
        timeout: cdk.Duration.seconds(5), // Cognito waits at most 5 seconds for a trigger
        ...extra,
      });
    users.addTrigger(cognito.UserPoolOperation.CUSTOM_MESSAGE, trigger('EmailTemplates', 'custom-message.handler'));

    if (emailCodes) {
      const senderRole = new iam.Role(this, 'OtpSenderRole', {
        roleName: OTP_SENDER_ROLE_NAME,
        assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
        managedPolicies: [iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole')],
      });
      senderRole.addToPolicy(new iam.PolicyStatement({ actions: ['sts:AssumeRole'], resources: [props.sesRoleArn!] }));
      users.addTrigger(cognito.UserPoolOperation.DEFINE_AUTH_CHALLENGE, trigger('DefineChallenge', 'define-challenge.handler'));
      users.addTrigger(
        cognito.UserPoolOperation.CREATE_AUTH_CHALLENGE,
        trigger('CreateChallenge', 'create-challenge.handler', {
          role: senderRole,
          environment: { SES_ROLE_ARN: props.sesRoleArn!, SES_REGION: this.region, FROM_ADDRESS: `no-reply@${domainName}` },
        }),
      );
      users.addTrigger(cognito.UserPoolOperation.VERIFY_AUTH_CHALLENGE_RESPONSE, trigger('VerifyChallenge', 'verify-challenge.handler'));
    }

    const loginDomain = users.addDomain('Login', {
      cognitoDomain: { domainPrefix: `pantry-pilot-${this.account}` },
      managedLoginVersion: cognito.ManagedLoginVersion.CLASSIC_HOSTED_UI,
    });
    const userScopes = [
      new cognito.ResourceServerScope({ scopeName: 'mcp:tools', scopeDescription: 'Use Pantry Pilot tools' }),
      new cognito.ResourceServerScope({ scopeName: 'mcp:resources', scopeDescription: 'Read Pantry Pilot resources' }),
    ];
    const serviceScope = new cognito.ResourceServerScope({ scopeName: 'mcp:service', scopeDescription: 'Service-level access' });
    const resourceServer = users.addResourceServer('McpResourceServer', {
      identifier: 'pantry-pilot',
      scopes: [...userScopes, serviceScope],
    });
    const userOAuthScopes = userScopes.map((s) => cognito.OAuthScope.resourceServer(resourceServer, s));

    // Alexa+ account linking: confidential client, authorization code + PKCE.
    // Its redirect URLs come from the Alexa+ add-on setup; pass them with -c alexaCallbacks=url1,url2.
    const alexaCallbacks = String(this.node.tryGetContext('alexaCallbacks') ?? 'https://localhost/alexa-callback-placeholder')
      .split(',')
      .map((u) => u.trim())
      .filter(Boolean);
    const alexaClient = users.addClient('AlexaClient', {
      userPoolClientName: 'alexa-plus',
      generateSecret: true,
      oAuth: { flows: { authorizationCodeGrant: true }, scopes: userOAuthScopes, callbackUrls: alexaCallbacks },
      accessTokenValidity: cdk.Duration.hours(1),
      refreshTokenValidity: cdk.Duration.days(30),
    });
    // Local testing (MCP Inspector, scripts): public client with PKCE and localhost redirects.
    const devClient = users.addClient('DevClient', {
      userPoolClientName: 'local-dev',
      generateSecret: false,
      oAuth: {
        flows: { authorizationCodeGrant: true },
        scopes: userOAuthScopes,
        callbackUrls: ['http://localhost/callback', 'http://localhost:6274/oauth/callback', 'http://127.0.0.1/callback'],
      },
    });
    // The web app's own sign-in: emailed one-time codes (custom auth), plus password for the judges'
    // demo account. Public client: a browser can't keep a secret. Users who link Alexa+ with the
    // same login share one pantry.
    const webClient = users.addClient('WebClient', {
      userPoolClientName: 'web-app',
      generateSecret: false,
      authFlows: { userPassword: true, custom: true },
      preventUserExistenceErrors: true,
      accessTokenValidity: cdk.Duration.hours(1),
      idTokenValidity: cdk.Duration.hours(1),
      refreshTokenValidity: cdk.Duration.days(30),
    });
    // Service-level (machine-to-machine) access for Alexa+ Tier 1 auth.
    const serviceClient = users.addClient('ServiceClient', {
      userPoolClientName: 'alexa-plus-service',
      generateSecret: true,
      oAuth: {
        flows: { clientCredentials: true },
        scopes: [cognito.OAuthScope.resourceServer(resourceServer, serviceScope)],
      },
      accessTokenValidity: cdk.Duration.hours(1),
    });

    const server = new lambda.Function(this, 'Server', {
      description: 'Pantry Pilot MCP server, Nova agent and web API',
      runtime: lambda.Runtime.PYTHON_3_13,
      architecture: lambda.Architecture.ARM_64,
      handler: 'run.sh',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'build', 'lambda')),
      layers: [
        lambda.LayerVersion.fromLayerVersionArn(
          this,
          'WebAdapter',
          `arn:aws:lambda:${this.region}:753240598075:layer:LambdaAdapterLayerArm64:${WEB_ADAPTER_LAYER_VERSION}`,
        ),
      ],
      memorySize: 1024,
      timeout: cdk.Duration.seconds(120),
      logGroup: new logs.LogGroup(this, 'ServerLogs', {
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
      environment: {
        AWS_LAMBDA_EXEC_WRAPPER: '/opt/bootstrap',
        AWS_LWA_INVOKE_MODE: 'response_stream',
        AWS_LWA_READINESS_CHECK_PATH: '/health',
        PORT: '8080',
        PANTRY_TABLE: table.tableName,
        HOUSEHOLD_ID: 'demo',
        NOVA_MODE: props.novaMode,
        NOVA_MODEL: 'global.amazon.nova-2-lite-v1:0',
        ORIGIN_SECRET: props.originSecret,
        COGNITO_USER_POOL_ID: users.userPoolId,
        COGNITO_DOMAIN: loginDomain.baseUrl(),
        COGNITO_WEB_CLIENT_ID: webClient.userPoolClientId,
        ...(emailCodes ? { COGNITO_EMAIL_OTP: 'on' } : {}),
        ...(props.novaRoleArn ? { NOVA_ROLE_ARN: props.novaRoleArn } : {}),
        INSTACART_KEY_PARAM,
        INSTACART_ENV: props.instacartEnv ?? 'development',
        KROGER_CREDENTIALS_PARAM,
        KROGER_ENV: 'production',
      },
    });
    // Instacart and Kroger credentials (SSM SecureStrings, encrypted with the AWS-managed aws/ssm key).
    server.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['ssm:GetParameter'],
        resources: [INSTACART_KEY_PARAM, KROGER_CREDENTIALS_PARAM].map(
          (name) => `arn:aws:ssm:${this.region}:${this.account}:parameter${name}`,
        ),
      }),
    );
    if (props.novaRoleArn) {
      server.addToRolePolicy(new iam.PolicyStatement({ actions: ['sts:AssumeRole'], resources: [props.novaRoleArn] }));
    }
    table.grantReadWriteData(server);
    // /api/auth/start creates an account the first time someone asks for a sign-in code.
    server.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['cognito-idp:AdminGetUser', 'cognito-idp:AdminCreateUser', 'cognito-idp:AdminSetUserPassword'],
        resources: [users.userPoolArn],
      }),
    );
    server.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
        // Global cross-region inference profiles route to foundation models in any region.
        resources: [
          `arn:aws:bedrock:*:${this.account}:inference-profile/*`,
          'arn:aws:bedrock:*::foundation-model/amazon.nova-*',
        ],
      }),
    );

    // Hands-free voice: the server signs Transcribe streaming URLs that the browser connects to.
    server.addToRolePolicy(
      new iam.PolicyStatement({ actions: ['transcribe:StartStreamTranscriptionWebSocket'], resources: ['*'] }),
    );

    const serverUrl = server.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.NONE,
      invokeMode: lambda.InvokeMode.RESPONSE_STREAM,
    });

    const site = new s3.Bucket(this, 'WebBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // The Lambda URL needs its own Host header, so pass the public host on for OAuth metadata URLs.
    const forwardHost = new cloudfront.Function(this, 'ForwardHost', {
      runtime: cloudfront.FunctionRuntime.JS_2_0,
      code: cloudfront.FunctionCode.fromInline(
        "function handler(event) { var r = event.request; r.headers['x-forwarded-host'] = { value: r.headers.host.value }; return r; }",
      ),
    });

    const api: cloudfront.BehaviorOptions = {
      origin: new origins.FunctionUrlOrigin(serverUrl, {
        customHeaders: { 'x-origin-verify': props.originSecret },
        readTimeout: cdk.Duration.seconds(60),
      }),
      allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
      cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
      originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      functionAssociations: [{ function: forwardHost, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST }],
    };

    const certificate = zone
      ? new acm.Certificate(this, 'Certificate', {
          domainName: domainName!,
          subjectAlternativeNames: [`www.${domainName}`],
          validation: acm.CertificateValidation.fromDns(zone),
        })
      : undefined;

    const distribution = new cloudfront.Distribution(this, 'Cdn', {
      comment: 'Pantry Pilot web app and API',
      ...(certificate && { domainNames: [domainName!, `www.${domainName}`], certificate }),
      defaultRootObject: 'index.html',
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(site),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      },
      additionalBehaviors: {
        '/api/*': api,
        '/mcp': api,
        '/mcp/*': api,
        '/health': api,
        '/.well-known/*': api,
        '/oauth/*': api,
      },
    });

    new s3deploy.BucketDeployment(this, 'WebDeploy', {
      sources: [s3deploy.Source.asset(path.join(__dirname, '..', '..', 'web', 'dist'))],
      destinationBucket: site,
      distribution,
      distributionPaths: ['/*'],
    });

    if (zone) {
      const target = route53.RecordTarget.fromAlias(new route53Targets.CloudFrontTarget(distribution));
      new route53.ARecord(this, 'AppRecord', { zone, target });
      new route53.AaaaRecord(this, 'AppRecordV6', { zone, target });
      new route53.ARecord(this, 'WwwRecord', { zone, recordName: 'www', target });
    }

    const appUrl = `https://${domainName ?? distribution.distributionDomainName}`;
    new cdk.CfnOutput(this, 'AppUrl', { value: appUrl, description: 'Pantry Pilot web app' });
    new cdk.CfnOutput(this, 'McpUrl', { value: `${appUrl}/mcp`, description: 'MCP endpoint for Alexa+' });
    new cdk.CfnOutput(this, 'TableName', { value: table.tableName });
    new cdk.CfnOutput(this, 'UserPoolId', { value: users.userPoolId });
    new cdk.CfnOutput(this, 'LoginDomain', { value: loginDomain.baseUrl() });
    new cdk.CfnOutput(this, 'AlexaClientId', { value: alexaClient.userPoolClientId });
    new cdk.CfnOutput(this, 'DevClientId', { value: devClient.userPoolClientId });
    new cdk.CfnOutput(this, 'WebClientId', { value: webClient.userPoolClientId });
    new cdk.CfnOutput(this, 'ServiceClientId', { value: serviceClient.userPoolClientId });
  }
}
