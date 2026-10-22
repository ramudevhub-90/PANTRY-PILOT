import { randomBytes } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import * as path from 'node:path';
import * as cdk from 'aws-cdk-lib';
import { PantryPilotStack } from '../lib/pantry-pilot-stack';

// Kept in a gitignored file so it stays the same across deploys without living in the repo.
const secretFile = path.join(__dirname, '..', '.origin-secret');
if (!existsSync(secretFile)) writeFileSync(secretFile, randomBytes(24).toString('hex'));
const originSecret = readFileSync(secretFile, 'utf8').trim();

const app = new cdk.App();
const novaMode = app.node.tryGetContext('nova') === 'mock' ? 'mock' : 'bedrock';
// Temporary until this account's Bedrock quota is raised: -c novaRole=arn:aws:iam::<other>:role/...
const novaRoleArn: string | undefined = app.node.tryGetContext('novaRole') || undefined;
// Our domain (hosted zone in this account): -c domain=trypantrypilot.com
const domainName: string | undefined = app.node.tryGetContext('domain') || undefined;
// Email-code sign-in, sending through SES in another account: -c sesRole=arn:aws:iam::<account>:role/<name>
const sesRoleArn: string | undefined = app.node.tryGetContext('sesRole') || undefined;
// Instacart Developer Platform keys: development until Instacart approves production (-c instacart=production).
// WhatsApp order updates: -c whatsappPhone=phone-number-id-…
const whatsappPhoneId: string | undefined = app.node.tryGetContext('whatsappPhone') || undefined;
// Fixed sign-in code for the judges' demo account, from the DEMO_CODE GitHub secret (off when unset).
const demoCode: string | undefined = process.env.DEMO_CODE || undefined;
const instacartEnv = app.node.tryGetContext('instacart') === 'production' ? 'production' : 'development';

new PantryPilotStack(app, 'PantryPilot', {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: 'us-east-1' },
  description: 'Pantry Pilot: kitchen agent for Alexa+ (MCP server on Lambda, web app on CloudFront)',
  originSecret,
  novaMode,
  novaRoleArn,
  domainName,
  sesRoleArn,
  instacartEnv,
  whatsappPhoneId,
  demoCode,
});
