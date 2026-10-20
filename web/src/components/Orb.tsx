import type { AssistantMode } from '../types';

/** Echo-style light ring that reflects what the assistant is doing. */
export function Orb({ mode }: { mode: AssistantMode }) {
  return (
    <div className="orb" data-mode={mode} aria-hidden="true">
      <div className="orb__glow" />
      <div className="orb__ring" />
      <div className="orb__core" />
    </div>
  );
}
