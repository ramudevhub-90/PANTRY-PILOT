import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, Camera, ExternalLink, Send } from 'lucide-react';
import { api, type WhatsAppReply } from '../agent/remote';
import { downscaleImage } from '../lib/media';
import { usePantry } from '../state/store';

type Bubble =
  | { from: 'me'; text: string; image?: string }
  | ({ from: 'bot' } & WhatsAppReply);

const time = () => new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

/**
 * Pantry Pilot's WhatsApp chat, inside the app. The server runs exactly the replies the WhatsApp
 * number sends (server/pantry_pilot/app.py, whatsapp_reply), so this works while Meta reviews the number.
 */
export function WhatsAppPreview({ onClose }: { onClose: () => void }) {
  const { dispatch } = usePantry();
  const [bubbles, setBubbles] = useState<(Bubble & { at: string })[]>([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const started = useRef(false);

  // Braces matter: newer browsers return a Promise from scrollIntoView, and React would call it as a cleanup.
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' });
  }, [bubbles, busy]);

  async function send(message: { text?: string; button?: string; image?: string }, shown: Bubble) {
    setBubbles((b) => [...b, { ...shown, at: time() }]);
    setBusy(true);
    try {
      const { messages, state } = await api.whatsappPreview(message);
      dispatch({ type: 'sync_state', state });
      setBubbles((b) => [...b, ...messages.map((m) => ({ from: 'bot' as const, ...m, at: time() }))]);
    } catch (err) {
      setBubbles((b) => [...b, { from: 'bot', kind: 'text', text: (err as Error).message, at: time() }]);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void send({ text: 'Hi' }, { from: 'me', text: 'Hi' });
  }, []);

  async function onPhoto(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    const image =
      file.type === 'application/pdf'
        ? await new Promise<string>((resolve) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result));
            reader.readAsDataURL(file);
          })
        : await downscaleImage(file, 1600);
    void send({ image }, { from: 'me', text: file.type === 'application/pdf' ? `📄 ${file.name}` : '', image: file.type === 'application/pdf' ? undefined : image });
  }

  return (
    <div className="wa-overlay" role="dialog" aria-modal="true" aria-label="Pantry Pilot on WhatsApp">
      <div className="wa">
        <header className="wa__head">
          <button type="button" className="wa__back" onClick={onClose} aria-label="Close WhatsApp chat">
            <ArrowLeft size={20} />
          </button>
          <div className="wa__avatar" aria-hidden="true">🥫</div>
          <div className="wa__title">
            <strong>Pantry Pilot</strong>
            <span>{busy ? 'typing…' : 'Business account'}</span>
          </div>
        </header>

        <div className="wa__body">
          <p className="wa__notice">Messages to Pantry Pilot's WhatsApp number get these same replies.</p>
          {bubbles.map((b, i) =>
            b.from === 'me' ? (
              <div key={i} className="wa__msg wa__msg--me">
                {b.image && <img src={b.image} alt="Photo sent" />}
                {b.text && <p>{b.text}</p>}
                <time>{b.at}</time>
              </div>
            ) : (
              <div key={i} className="wa__group">
                <div className="wa__msg wa__msg--bot">
                  <p>{b.text}</p>
                  <time>{b.at}</time>
                </div>
                {b.kind === 'buttons' &&
                  b.buttons.map((btn) => (
                    <button
                      key={btn.id}
                      type="button"
                      className="wa__reply"
                      disabled={busy}
                      onClick={() => void send({ button: btn.id }, { from: 'me', text: btn.title })}
                    >
                      {btn.title}
                    </button>
                  ))}
                {b.kind === 'link' && (
                  <a className="wa__reply" href={b.url} onClick={onClose}>
                    <ExternalLink size={14} aria-hidden="true" /> {b.label}
                  </a>
                )}
              </div>
            ),
          )}
          {busy && <div className="wa__msg wa__msg--bot wa__typing" aria-label="Pantry Pilot is typing"><span /><span /><span /></div>}
          <div ref={endRef} />
        </div>

        <form
          className="wa__input"
          onSubmit={(e) => {
            e.preventDefault();
            const text = draft.trim();
            if (!text || busy) return;
            setDraft('');
            void send({ text }, { from: 'me', text });
          }}
        >
          <button type="button" className="wa__icon" onClick={() => fileRef.current?.click()} disabled={busy} aria-label="Send a photo or bill">
            <Camera size={20} />
          </button>
          <input ref={fileRef} type="file" accept="image/*,application/pdf" hidden onChange={onPhoto} />
          <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Message" aria-label="Message Pantry Pilot" />
          <button type="submit" className="wa__send" disabled={!draft.trim() || busy} aria-label="Send">
            <Send size={18} />
          </button>
        </form>
      </div>
    </div>
  );
}
