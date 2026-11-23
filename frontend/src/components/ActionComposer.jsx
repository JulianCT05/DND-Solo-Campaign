import { useEffect, useState } from 'react';

export default function ActionComposer({ state, busy, disabled, selectedTarget, onSubmit, onCommand }) {
  const [kind, setKind] = useState('move');
  const [x, setX] = useState('6');
  const [y, setY] = useState('6');
  const [target, setTarget] = useState('');
  const [spell, setSpell] = useState('');
  const [feature, setFeature] = useState('rage');
  const [objectId, setObjectId] = useState('');
  const [verb, setVerb] = useState('open');
  const [command, setCommand] = useState('');
  const [localError, setLocalError] = useState('');

  const player = state?.party?.find(p => p.controller === 'player') || state?.party?.[0];
  const enemies = (state?.enemies || []).filter(e => e.alive);
  const friendlies = (state?.party || []).filter(p => p.alive);
  const targets = [...enemies, ...friendlies];
  const spells = player?.resources?.prepared_spells || [];
  const features = Object.keys(player?.resources?.uses || {});
  const featuresOnMap = (state?.map?.features || []).filter(f => f.state !== 'hidden');
  const effectiveTarget = target || selectedTarget || enemies[0]?.id || '';

  useEffect(() => { if (selectedTarget) setTarget(selectedTarget); }, [selectedTarget]);
  useEffect(() => { if (spells.length && !spells.includes(spell)) setSpell(spells[0]); }, [player?.id, spells.join('|')]);

  function doAction(e) {
    e.preventDefault();
    setLocalError('');
    let a;
    if (kind === 'move') {
      const mx = Number(x), my = Number(y);
      if (x.trim() === '' || y.trim() === '' || !Number.isInteger(mx) || !Number.isInteger(my) || mx < 0 || my < 0) { setLocalError('Enter two non-negative whole-number coordinates.'); return; }
      a = { type: 'move', to: { x: mx, y: my } };
    } else if (kind === 'attack') {
      if (!effectiveTarget || !enemies.some(e => e.id === effectiveTarget)) { setLocalError('Choose a living enemy.'); return; }
      a = { type: 'attack', target_id: effectiveTarget };
    } else if (kind === 'cast') {
      const id = spell.trim();
      if (!id) { setLocalError('Enter or select a spell ID.'); return; }
      a = { type: 'cast', spell_id: id, ...(target ? { target_ids: [target] } : {}) };
    } else if (kind === 'use_feature') {
      if (!feature.trim()) { setLocalError('Enter a feature ID.'); return; }
      a = { type: 'use_feature', feature: feature.trim() };
    } else if (kind === 'interact') {
      if (!objectId) { setLocalError('Select a nearby map feature.'); return; }
      a = { type: 'interact', object_id: objectId, verb };
    } else if (kind === 'rest') {
      a = { type: 'rest', kind: 'short' };
    } else {
      a = { type: kind };
    }
    onSubmit([a], `GUI: ${JSON.stringify(a)}`);
  }
  function sendText(e) {
    e.preventDefault();
    const text = command.trim();
    if (!text) return;
    setLocalError('');
    onCommand(text);
    setCommand('');
  }
  return <section className="panel command-panel" aria-labelledby="commands-title">
    <div className="panel-top"><span className="eyebrow">Your decisions</span><h2 id="commands-title">Make Your Move</h2><p className="tiny">Only your character receives manual commands. Enemies and allies take their turns through the engine.</p></div>
    <form className="action-form" onSubmit={doAction}>
      <label>Action type<select value={kind} onChange={e => {setKind(e.target.value);setLocalError('');}} disabled={disabled || busy}>
        <option value="move">Move</option><option value="attack">Attack</option><option value="cast">Cast spell</option><option value="use_feature">Use feature</option><option value="interact">Interact</option><option value="dash">Dash</option><option value="dodge">Dodge</option><option value="disengage">Disengage</option><option value="search">Search</option><option value="rest">Short rest</option><option value="end_turn">End turn</option>
      </select></label>
      {kind === 'move' && <div className="coordinate-row"><label>X<input type="number" min="0" step="1" value={x} onChange={e=>setX(e.target.value)} disabled={disabled || busy}/></label><label>Y<input type="number" min="0" step="1" value={y} onChange={e=>setY(e.target.value)} disabled={disabled || busy}/></label></div>}
      {kind === 'attack' && <label>Enemy target<select value={effectiveTarget} onChange={e=>setTarget(e.target.value)} disabled={disabled || busy}>{!enemies.length && <option value="">No living enemies</option>}{enemies.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></label>}
      {kind === 'cast' && <><label>Spell ID<input list="spells" value={spell} onChange={e=>setSpell(e.target.value)} placeholder="e.g. sacred_flame" disabled={disabled || busy}/><datalist id="spells">{spells.map(s=><option key={s} value={s}/>)}</datalist></label><label>Target (optional)<select value={target} onChange={e=>setTarget(e.target.value)} disabled={disabled || busy}><option value="">No target</option>{targets.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></label></>}
      {kind === 'use_feature' && <label>Feature ID<input value={feature} list="features" onChange={e=>setFeature(e.target.value)} disabled={disabled || busy}/><datalist id="features">{features.map(f=><option key={f} value={f}/>)}</datalist></label>}
      {kind === 'interact' && <><label>Feature<select value={objectId} onChange={e=>setObjectId(e.target.value)} disabled={disabled || busy}><option value="">Choose a feature</option>{featuresOnMap.map(f=><option value={f.id} key={f.id}>{f.kind} — {f.id}</option>)}</select></label><label>Interaction<select value={verb} onChange={e=>setVerb(e.target.value)} disabled={disabled || busy}>{['open','close','unlock','force','disarm','pull','take','examine'].map(v=><option key={v} value={v}>{v}</option>)}</select></label></>}
      <button type="submit" className="primary" disabled={disabled || busy}>{busy ? 'Resolving…' : 'Perform Action'}</button>
    </form>
    <div className="divider-line" />
    <form onSubmit={sendText} className="typed-form"><label htmlFor="command-text">Or type an exact command</label><div className="text-send"><input id="command-text" value={command} onChange={e=>setCommand(e.target.value)} placeholder="end turn • rage • move 6 6" disabled={disabled || busy}/><button type="submit" disabled={disabled || busy || !command.trim()}>Send</button></div></form>
    <p className="small-note">Natural-language AI parsing is not connected yet; use the action controls or supported exact commands.</p>
    {localError && <div className="form-error" role="alert">{localError}</div>}
  </section>;
}
