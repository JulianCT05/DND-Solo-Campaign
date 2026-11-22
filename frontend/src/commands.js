// A strict, intentionally small fallback. Never guess ambiguous natural language.
// Replace the fallback with Josh's parser when its endpoint is available.
export function parseCommand(input, state) {
  const s=input.trim();
  let m;
  if (/^(end\s*turn|pass)$/i.test(s)) return [{type:'end_turn'}];
  if (/^dash$/i.test(s)) return [{type:'dash'}];
  if (/^dodge$/i.test(s)) return [{type:'dodge'}];
  if (/^disengage$/i.test(s)) return [{type:'disengage'}];
  if (/^hide$/i.test(s)) return [{type:'hide'}];
  if (/^search$/i.test(s)) return [{type:'search'}];
  if (/^stand\s*up$/i.test(s)) return [{type:'stand_up'}];
  if ((m=/^move\s+(\d+)\s*[, ]\s*(\d+)$/i.exec(s))) return [{type:'move',to:{x:Number(m[1]),y:Number(m[2])}}];
  if ((m=/^attack\s+([\w-]+)$/i.exec(s))) {
    const target=(state.enemies || []).find(e=>e.id===m[1] || e.name?.toLowerCase()===m[1].toLowerCase());
    if(!target)throw new Error('Use an exact enemy ID after "attack" or click its token.');
    return [{type:'attack',target_id:target.id}];
  }
  if ((m=/^(rage|channel_divinity|adrenaline_rush|second_wind)$/i.exec(s))) return [{type:'use_feature',feature:m[1].toLowerCase()}];
  if ((m=/^rest\s+(short|long)$/i.exec(s))) return [{type:'rest',kind:m[1].toLowerCase()}];
  if ((m=/^(open|close|unlock|force|disarm|pull|take|examine)\s+([\w-]+)$/i.exec(s)))return [{type:'interact',verb:m[1].toLowerCase(),object_id:m[2]}];
  throw new Error('This GUI currently supports exact commands: "move 3 4", "attack ENEMY_ID", "end turn", "rage", "dodge", "dash", "rest short", or "open OBJECT_ID". Free-form language needs Josh\'s parser API.');
}