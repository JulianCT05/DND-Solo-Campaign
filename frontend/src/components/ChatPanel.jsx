import {useState} from 'react';
export default function ChatPanel({lines,onSend,busy,disabled}) {
  const [text,setText]=useState('');
  function submit(e) { e.preventDefault(); if(!text.trim() || busy || disabled)return; onSend(text.trim());setText(''); }
  return <div className="chat"><div className="chat-history" aria-live="polite">{lines.map((line,i)=><div key={i} className={`bubble ${line.role}`}>{line.text}</div>)}</div><form className="chat-form" onSubmit={submit}><input value={text} onChange={e=>setText(e.target.value)} placeholder="Try: attack giant_frog_2, move 3 4, end turn…" aria-label="Player command" disabled={disabled || busy}/><button type="submit" disabled={disabled || busy || !text.trim()}>Send</button></form><p className="hint">Basic command mode only. Josh's LLM parser and narrator require their own integration endpoint.</p></div>;
}
