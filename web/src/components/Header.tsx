import { useEffect, useState, useSyncExternalStore } from 'react';
import { ChefHat, Cpu, LogOut, UserRound, Wrench } from 'lucide-react';
import { HAS_SERVER, api, type ServerHealth } from '../agent/remote';
import { currentEmail, signOut, subscribe } from '../auth/cognito';
import { TOOLS } from '../agent/tools';

type Status = { kind: 'mock' } | { kind: 'checking' } | { kind: 'offline' } | ({ kind: 'online' } & ServerHealth);

const LABELS = {
  mock: { text: 'In-browser demo', tone: 'warn', hint: 'No server configured: replies come from the in-browser mock agent' },
  checking: { text: 'Connecting…', tone: 'warn', hint: 'Contacting the Pantry Pilot server' },
  offline: { text: 'Server offline', tone: 'bad', hint: `Can't reach the Pantry Pilot server` },
} as const;

export function Header() {
  const email = useSyncExternalStore(subscribe, currentEmail);
  const [status, setStatus] = useState<Status>(HAS_SERVER ? { kind: 'checking' } : { kind: 'mock' });

  useEffect(() => {
    if (!HAS_SERVER) return;
    const check = () =>
      api
        .health()
        .then((h) => setStatus({ kind: 'online', ...h }))
        .catch(() => setStatus({ kind: 'offline' }));
    check();
    const id = setInterval(check, 15_000);
    return () => clearInterval(id);
  }, []);

  const label =
    status.kind === 'online'
      ? status.nova === 'bedrock'
        ? { text: 'Live · Amazon Nova', tone: 'ok', hint: `MCP server connected, model ${status.model}` }
        : { text: 'Server · Nova in mock mode', tone: 'warn', hint: 'MCP server connected; Nova calls are simulated (NOVA_MODE=mock)' }
      : LABELS[status.kind];
  const tools = status.kind === 'online' ? status.tools : Object.keys(TOOLS).length;

  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand__mark">
          <ChefHat size={20} />
        </span>
        <div>
          <h1>Pantry Pilot</h1>
          <p>Kitchen agent for Alexa+</p>
        </div>
      </div>
      <div className="topbar__meta">
        <span className="pill" title={label.hint}>
          <span className={`dot dot--${label.tone}`} />
          {label.text}
        </span>
        <span className="pill pill--quiet">
          <Cpu size={14} />
          Amazon Nova 2 Lite
        </span>
        <span className="pill pill--quiet">
          <Wrench size={14} />
          {tools} MCP tools
        </span>
        {email && (
          <span className="user-menu">
            <span className="user-menu__email" title={email}>
              <UserRound size={14} />
              {email}
            </span>
            <button type="button" className="btn btn--secondary btn--sm" onClick={signOut}>
              <LogOut size={14} />
              Sign out
            </button>
          </span>
        )}
      </div>
    </header>
  );
}
