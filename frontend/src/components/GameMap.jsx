import { useEffect, useRef } from 'react';
const CELL = 43;
const COLORS = { '.':'#293848', '#':'#101924', '~':'#456576', 'D':'#a27344', 'W':'#286385' };
const FEATURE_COLORS = { chest:'#e6b85a', trap:'#de6d6d', door:'#b58b56', lever:'#d7bc77', exit:'#69cebc', npc:'#8ddaa7', cover:'#8d9198', hazard:'#d36e97' };

export default function GameMap({ state, reachable, activePlayerId, busy, onCell }) {
  const canvas = useRef(null);
  const map = state?.map;
  useEffect(() => {
    if (!map || !canvas.current) return;
    const ctx = canvas.current.getContext('2d');
    ctx.clearRect(0, 0, map.width * CELL, map.height * CELL);
    const canReach = new Set(reachable.map(p => `${p.x},${p.y}`));
    for (let y = 0; y < map.height; y++) {
      for (let x = 0; x < map.width; x++) {
        const tile = map.terrain?.[y]?.[x] ?? '.';
        const terrainName = map.legend?.[tile] ?? '';
        const inferred = /wall/i.test(terrainName) ? '#' : /door/i.test(terrainName) ? 'D' : /water/i.test(terrainName) ? 'W' : /difficult/i.test(terrainName) ? '~' : tile;
        ctx.fillStyle = COLORS[inferred] || '#293848';
        ctx.fillRect(x*CELL, y*CELL, CELL, CELL);
        ctx.strokeStyle = '#53627355';
        ctx.strokeRect(x*CELL+.5, y*CELL+.5, CELL-1, CELL-1);
        if (canReach.has(`${x},${y}`)) {
          ctx.fillStyle = '#67d9a444'; ctx.fillRect(x*CELL+2,y*CELL+2,CELL-4,CELL-4);
          ctx.strokeStyle = '#71e2a4'; ctx.strokeRect(x*CELL+2.5,y*CELL+2.5,CELL-5,CELL-5);
        }
        if (inferred === 'D') { ctx.fillStyle = '#dab77d'; ctx.fillRect(x*CELL+17,y*CELL+4,9,CELL-8); }
        if (inferred === '~' || inferred === 'W') {
          ctx.fillStyle = '#b0d5e7'; ctx.font='16px sans-serif';ctx.fillText('≈', x*CELL+15,y*CELL+27);
        }
      }
    }
    for (const area of map.areas || []) {
      const {x,y} = area.origin || {};
      if (x == null || y == null) continue;
      ctx.fillStyle = '#ad7ce399';ctx.fillRect(x*CELL+4,y*CELL+4,CELL-8,CELL-8);
      ctx.strokeStyle = '#e5b7ff';ctx.strokeRect(x*CELL+3,y*CELL+3,CELL-6,CELL-6);
    }
    for (const feature of map.features || []) {
      if (feature.state === 'hidden') continue;
      const {x,y} = feature.position || {};
      if(x == null || y == null)continue;
      ctx.fillStyle = FEATURE_COLORS[feature.kind] || '#c3a8d8';
      ctx.fillRect(x*CELL+13,y*CELL+13,17,17);
      ctx.fillStyle='#111b25';ctx.font='bold 12px sans-serif';ctx.textAlign='center';
      ctx.fillText((feature.kind || '?')[0].toUpperCase(),x*CELL+21.5,y*CELL+26);
    }
    const combatants = [...(state.party || []), ...(state.enemies || [])];
    for (const c of combatants) {
      if(!c.position || !c.alive)continue;
      const {x,y} = c.position;
      const isEnemy = (state.enemies || []).some(e => e.id === c.id);
      const isPlayer = c.id === activePlayerId;
      ctx.beginPath();ctx.arc(x*CELL+CELL/2,y*CELL+CELL/2,15,0,Math.PI*2);
      ctx.fillStyle = isEnemy ? '#dc5264' : isPlayer ? '#4e9eff' : '#66bd91';ctx.fill();
      ctx.lineWidth=2.5;ctx.strokeStyle='#f3f1dd';ctx.stroke();
      ctx.fillStyle='#fff';ctx.font='bold 13px sans-serif';ctx.textAlign='center';
      ctx.fillText((c.name || c.id).slice(0,1).toUpperCase(),x*CELL+CELL/2,y*CELL+CELL/2+5);
    }
    ctx.textAlign='start';
  }, [map, state, reachable, activePlayerId]);

  if (!map) return <div className="empty">Start a game to load the room map.</div>;
  return <div className="map-scroll"><canvas
    ref={canvas} width={map.width * CELL} height={map.height * CELL}
    onClick={(e) => {
      if (busy) return;
      const rect = e.currentTarget.getBoundingClientRect();
      const x = Math.floor((e.clientX - rect.left) * e.currentTarget.width / rect.width / CELL);
      const y = Math.floor((e.clientY - rect.top) * e.currentTarget.height / rect.height / CELL);
      if(x>=0 && y>=0 && x<map.width && y<map.height) onCell({x,y});
    }}
    aria-label="Interactive dungeon grid; select reachable squares or enemy tokens" role="img"
    style={{ width: map.width*CELL, height: map.height*CELL }}
  /></div>;
}
