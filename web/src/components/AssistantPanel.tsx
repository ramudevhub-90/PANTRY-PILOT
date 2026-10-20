import { useEffect, useRef, useState } from 'react';
import { Check, ChevronRight, Ear, EarOff, Loader2, Mic, SendHorizontal, Square, Volume2, VolumeX } from 'lucide-react';
import { Orb } from './Orb';
import { ScanButton } from './ScanButton';
import { useAssistant } from '../agent/useAssistant';
import { useHandsFree } from '../hooks/useHandsFree';
import { stopSpeaking, useSpeechRecognition } from '../hooks/useSpeech';
import { usePantry } from '../state/store';
import type { AssistantMode, ChatMessage, ToolCall } from '../types';

const SUGGESTIONS = [
  'What can I make right now?',
  "What's about to expire?",
  "We're out of milk",
  'Set a 10 minute timer for the pasta',
];

const HANDS_FREE_KEY = 'pantry-pilot.handsFree';

const STATUS: Record<AssistantMode, string> = {
  idle: 'Tap the mic or type a request',
  listening: 'Listening…',
  thinking: 'Thinking…',
  speaking: 'Speaking…',
};

export function AssistantPanel() {
  const { state, dispatch, getState } = usePantry();
  const { ask } = useAssistant();
  const [draft, setDraft] = useState('');
  const speech = useSpeechRecognition((text) => void ask(text));
  const busy = state.mode === 'thinking';
  const handsFree = useHandsFree((text) => void ask(text), busy || state.mode === 'speaking');
  const compact = state.messages.length > 0;
  // Browsers without speech recognition (Firefox) use the Transcribe stream for the mic button too.
  const micViaStream = !speech.supported && handsFree.supported;
  const listening = speech.listening || handsFree.phase === 'command';
  const interim = speech.listening ? speech.interim : handsFree.interim;

  useEffect(() => {
    if (listening) dispatch({ type: 'set_mode', mode: 'listening' });
    else if (getState().mode === 'listening') dispatch({ type: 'set_mode', mode: 'idle' });
  }, [listening, dispatch, getState]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    setDraft('');
    void ask(text);
  }

  function toggleMic() {
    if (speech.listening) {
      speech.stop();
    } else if (micViaStream && handsFree.phase === 'command') {
      handsFree.stop();
    } else {
      stopSpeaking();
      if (micViaStream) void handsFree.start('once');
      else speech.start();
    }
  }

  // When a reply ends with a question, listen for the answer without waiting for "Alexa" again.
  const expectAnswer = handsFree.expectAnswer;
  const previousMode = useRef(state.mode);
  useEffect(() => {
    const was = previousMode.current;
    previousMode.current = state.mode;
    if (state.mode !== 'idle' || (was !== 'speaking' && was !== 'thinking')) return;
    const last = state.messages[state.messages.length - 1];
    if (last?.role === 'assistant' && !last.pending && /\?\s*$/.test(last.text)) expectAnswer();
  }, [state.mode, state.messages, expectAnswer]);

  // Hands-free is on by default; switching it off is remembered on this device.
  const startHandsFree = handsFree.start;
  useEffect(() => {
    let off = false;
    try {
      off = localStorage.getItem(HANDS_FREE_KEY) === 'off';
    } catch {
      // Storage blocked: keep the default.
    }
    if (handsFree.supported && !off) void startHandsFree('wake');
  }, [handsFree.supported, startHandsFree]);

  function toggleHandsFree() {
    const turningOn = handsFree.phase === 'off';
    if (turningOn) void handsFree.start('wake');
    else handsFree.stop();
    try {
      localStorage.setItem(HANDS_FREE_KEY, turningOn ? 'on' : 'off');
    } catch {
      // Storage blocked: the choice lasts until the page reloads.
    }
  }

  const handsFreeStatus =
    handsFree.phase === 'starting' ? 'Starting the microphone…' : handsFree.phase === 'waiting' ? 'Say “Alexa” and your request' : null;
  const status =
    listening && interim
      ? `“${interim}”`
      : speech.error ??
        handsFree.error ??
        (state.mode === 'idle' && handsFreeStatus ? handsFreeStatus : STATUS[state.mode]);

  return (
    <aside className="assistant" aria-label="Alexa+ simulator">
      <div className={`device${compact ? ' device--compact' : ''}`}>
        <Orb mode={state.mode} />
        <div className="device__text">
          <p className="device__label">Alexa+ simulator</p>
          <p className="device__status" aria-live="polite">
            {status}
          </p>
        </div>
      </div>

      <Transcript messages={state.messages} onSuggestion={(s) => void ask(s)} disabled={busy} />

      <form className="composer" onSubmit={submit}>
        <ScanButton variant="icon" disabled={busy} />
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={listening ? 'Listening…' : 'Ask Pantry Pilot…'}
          aria-label="Message Pantry Pilot"
        />
        <button type="submit" className="icon-btn" disabled={!draft.trim() || busy} aria-label="Send">
          <SendHorizontal size={18} />
        </button>
        <button
          type="button"
          className={`mic${listening ? ' mic--on' : ''}`}
          onClick={toggleMic}
          disabled={!(speech.supported || micViaStream) || busy}
          aria-label={listening ? 'Stop listening' : 'Start voice input'}
          title={speech.supported || micViaStream ? undefined : 'Voice input isn’t available in this browser'}
        >
          {listening ? <Square size={16} fill="currentColor" /> : <Mic size={20} />}
        </button>
      </form>
      <div className="composer__foot">
        <button
          type="button"
          className="link-btn"
          onClick={() => {
            if (state.voiceReplies) stopSpeaking();
            dispatch({ type: 'set_voice', on: !state.voiceReplies });
          }}
          aria-pressed={state.voiceReplies}
        >
          {state.voiceReplies ? <Volume2 size={14} /> : <VolumeX size={14} />}
          Voice replies {state.voiceReplies ? 'on' : 'off'}
        </button>
        {handsFree.supported ? (
          <button
            type="button"
            className={`link-btn${handsFree.phase !== 'off' ? ' link-btn--on' : ''}`}
            onClick={toggleHandsFree}
            aria-pressed={handsFree.phase !== 'off'}
            title="Listens for “Alexa” using Amazon Transcribe while switched on"
          >
            {handsFree.phase !== 'off' ? <Ear size={14} /> : <EarOff size={14} />}
            Hands-free {handsFree.phase !== 'off' ? 'on' : 'off'}
          </button>
        ) : (
          <span>{speech.supported ? 'Uses your browser’s speech recognition' : 'Voice input needs Chrome or Safari'}</span>
        )}
      </div>
    </aside>
  );
}

function Transcript({ messages, onSuggestion, disabled }: { messages: ChatMessage[]; onSuggestion: (s: string) => void; disabled: boolean }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  }, [messages]);

  if (!messages.length) {
    return (
      <div className="transcript transcript--empty">
        <p className="eyebrow">Try saying</p>
        {SUGGESTIONS.map((s) => (
          <button key={s} type="button" className="suggestion" onClick={() => onSuggestion(s)} disabled={disabled}>
            “{s}”
          </button>
        ))}
      </div>
    );
  }

  return (
    <div className="transcript" ref={ref} role="log" aria-live="polite">
      {messages.map((m) => (
        <Message key={m.id} message={m} />
      ))}
    </div>
  );
}

function Message({ message }: { message: ChatMessage }) {
  if (message.role === 'user') {
    return (
      <div className="msg msg--user">
        {message.images && (
          <div className="msg__photos">
            {message.images.map((src, i) => (
              <img key={i} src={src} alt={`Grocery photo ${i + 1}`} />
            ))}
          </div>
        )}
        {message.text && <p>{message.text}</p>}
      </div>
    );
  }
  return (
    <div className="msg msg--assistant">
      {message.toolCalls.length > 0 && (
        <div className="trace">
          <p className="trace__label">MCP tool calls</p>
          <ul>
            {message.toolCalls.map((c) => (
              <ToolRow key={c.id} call={c} />
            ))}
          </ul>
        </div>
      )}
      {message.pending && !message.text ? (
        <span className="typing" aria-label="Pantry Pilot is thinking">
          <span />
          <span />
          <span />
        </span>
      ) : (
        <p>{message.text}</p>
      )}
    </div>
  );
}

function ToolRow({ call }: { call: ToolCall }) {
  const [open, setOpen] = useState(false);
  return (
    <li className={`tool tool--${call.status}`}>
      <button type="button" className="tool__head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="tool__icon">{call.status === 'running' ? <Loader2 size={13} className="spin" /> : <Check size={13} />}</span>
        <code>{call.name}</code>
        <span className="tool__summary">{call.summary ?? 'running…'}</span>
        {call.durationMs != null && <span className="tool__ms">{call.durationMs}ms</span>}
        <ChevronRight size={14} className="tool__chev" />
      </button>
      {open && <pre className="tool__args">{JSON.stringify(call.args, null, 2)}</pre>}
    </li>
  );
}
