"""Rzut parteru w stylu architektonicznym dla ciemnego panelu ściennego.

Obrysy pokoi z PLAN (gen_ha.py), drzwi i okna odczytane z PDF PAB „0. Parter PAB branż.” (pozycje przybliżone).
Wyjście (katalog z argv[1], domyślnie build/www/plan/, gitignore'd) — warstwy nakładane w panelu:
    rzut_podloga.svg   podłoga (pod nią wypełnienia oświetlenia pokoi)
    rzut_sciany.svg    ściany z otworami + okna
    rzut_drzwi.svg     skrzydła i łuki drzwi (warstwa opcjonalna)
    rooms.json         nazwa, punkt etykiety i obrys (clip-path) każdego pokoju w % rzutu

Zasady drzwi: każdy wiatrołap ma drzwi zewnętrzne (otwierane na zewnątrz) i wewnętrzne
(otwierane do wiatrołapu); wyjątek — warsztat i tech. suchy otwierają się do pomieszczeń,
żeby skrzydła nie zderzały się w wiatrołapie technicznym.

    python3 import/hapla-ha-addons/config/gen_plan.py [katalog]
"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_ha as g
PLAN = g.PLAN
K, M, X0, Y0 = 1.6, 8, 225, 187
X = lambda x: (x - X0) * K + M
Y = lambda y: (y - Y0) * K + M
FLOOR, WALL, ARC, WIN = '#17171f', '#dcdce6', '#7d7d92', '#9ec5ff'
EXT_W, INT_W = 14, 6

outline = [(225,187),(1012,187),(1012,249),(973,249),(973,350),(1012,350),(1012,646),(713.5,646),
           (713.5,577),(225,577),(225,468),(264,468),(264,350),(225,350)]
names = {1:'Wiatrołap',2:'Przedpokój',3:'Kuchnia',4:'Salon',5:'Hol',6:'Tech suchy',7:'Warsztat',
         8:'Tech mokry',9:'Pralnia',10:'Łazienka',12:'Sypialnia',13:'Garderoba',14:'Wiatrołap',
         15:'Pracownia',16:'Łazienka',17:'Gościnny',19:'Hol',20:'Wiatrołap'}

def on_outline(a, b):
    for i in range(len(outline)):
        p, q = outline[i], outline[(i + 1) % len(outline)]
        if p[0] == q[0] == a[0] == b[0] and min(p[1], q[1]) <= min(a[1], b[1]) and max(a[1], b[1]) <= max(p[1], q[1]):
            return True
        if p[1] == q[1] == a[1] == b[1] and min(p[0], q[0]) <= min(a[0], b[0]) and max(a[0], b[0]) <= max(p[0], q[0]):
            return True
    return False

# połączenia otwarte (jedna przestrzeń): bez ściany — odejmowane od każdego odcinka na tej samej linii
open_edges = [('h', 350, 563, 713.5),   # kuchnia | salon
              ('v', 444, 350, 408),     # salon | hol 19
              ('v', 713.5, 350, 408)]   # salon | hol 05

def minus_open(a, b):
    """Odcinek ściany a-b po odjęciu otwartych przejść → lista odcinków."""
    o, at, lo, hi = ('h', a[1], a[0], b[0]) if a[1] == b[1] else ('v', a[0], a[1], b[1])
    lo, hi = min(lo, hi), max(lo, hi)
    parts = [(lo, hi)]
    for eo, eat, elo, ehi in open_edges:
        if eo != o or eat != at:
            continue
        nxt = []
        for p, q in parts:
            if ehi <= p or elo >= q:
                nxt.append((p, q)); continue
            if p < elo: nxt.append((p, elo))
            if ehi < q: nxt.append((ehi, q))
        parts = nxt
    return [((p, at), (q, at)) if o == 'h' else ((at, p), (at, q)) for p, q in parts]

segs = set()
for _, pts in PLAN.values():
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        if on_outline(a, b):
            continue
        for s in minus_open(a, b):
            segs.add(tuple(sorted(s)))
extra = [((373,245),(435,245)), ((373,285),(435,285)),   # prysznic | sauna — od lewej ściany
          ((847,460),(907,460)), ((827,408),(827,433))]

# pole rysunku szersze niż rzut HA: drzwi zewnętrzne otwierają się na zewnątrz poza obrys
VX, VY, W, H = 0, -72, 1344, 822
SVG = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{VX} {VY} {W} {H}">'
pts = ' '.join(f'{X(x):g},{Y(y):g}' for x, y in outline)
floor = [SVG, f'<polygon points="{pts}" fill="{FLOOR}"/>']
walls, gaps, win, doors = [], [], [], []
for a, b in sorted(segs):
    walls.append(f'<line x1="{X(a[0]):g}" y1="{Y(a[1]):g}" x2="{X(b[0]):g}" y2="{Y(b[1]):g}" stroke="{WALL}" stroke-width="{INT_W}" stroke-linecap="square"/>')
for a, b in extra:
    walls.append(f'<line x1="{X(a[0]):g}" y1="{Y(a[1]):g}" x2="{X(b[0]):g}" y2="{Y(b[1]):g}" stroke="{WALL}" stroke-width="3" stroke-linecap="square"/>')
walls.append(f'<polygon points="{pts}" fill="none" stroke="{WALL}" stroke-width="{EXT_W}" stroke-linejoin="miter"/>')

def cut(o, at, a, b, ext):
    """Przerwa w ścianie (maska): o='h' (ściana pozioma y=at, x a..b) / 'v' (x=at, y a..b)."""
    t = (EXT_W if ext else INT_W) + 4
    if o == 'h':
        gaps.append(f'<rect x="{X(a):g}" y="{Y(at) - t/2:g}" width="{(b - a) * K:g}" height="{t}" fill="black"/>')
    else:
        gaps.append(f'<rect x="{X(at) - t/2:g}" y="{Y(a):g}" width="{t}" height="{(b - a) * K:g}" fill="black"/>')

def door(o, hx, hy, along, w, swing, ext=False):
    """Zawias (hx,hy); along=±1 kierunek zamka wzdłuż ściany; swing=±1 strona otwarcia."""
    if o == 'h':
        lx, ly = hx + along * w, hy
        ox, oy = hx, hy + swing * w
        cut('h', hy, min(hx, lx), max(hx, lx), ext)
    else:
        lx, ly = hx, hy + along * w
        ox, oy = hx + swing * w, hy
        cut('v', hx, min(hy, ly), max(hy, ly), ext)
    r = w * K
    # łuk wokół zawiasu: od zamka (L) do otwartego skrzydła (O); y w dół jak w SVG
    sweep = 1 if (lx - hx) * (oy - hy) - (ly - hy) * (ox - hx) > 0 else 0
    doors.append(f'<path d="M{X(lx):g},{Y(ly):g} A{r:g},{r:g} 0 0 {sweep} {X(ox):g},{Y(oy):g}" fill="none" stroke="{ARC}" stroke-width="1.6" stroke-dasharray="5 4"/>')
    doors.append(f'<line x1="{X(hx):g}" y1="{Y(hy):g}" x2="{X(ox):g}" y2="{Y(oy):g}" stroke="{WALL}" stroke-width="3.5" stroke-linecap="round"/>')

def sliding(o, at, a, b, n):
    """Drzwi przesuwne n-skrzydłowe: skrzydła na przemian po obu stronach osi ściany."""
    cut(o, at, a, b, False)
    L = (b - a) / n
    for i in range(n):
        p, q = a + i * L - (2 if i else 0), a + (i + 1) * L + (2 if i < n - 1 else 0)
        s = 3 if i % 2 else -3
        if o == 'h':
            doors.append(f'<line x1="{X(p):g}" y1="{Y(at) + s:g}" x2="{X(q):g}" y2="{Y(at) + s:g}" stroke="{WALL}" stroke-width="3"/>')
        else:
            doors.append(f'<line x1="{X(at) + s:g}" y1="{Y(p):g}" x2="{X(at) + s:g}" y2="{Y(q):g}" stroke="{WALL}" stroke-width="3"/>')

def pocket(o, at, a, b):
    """Drzwi przesuwne w kasecie: skrzydło chowa się w ścianie za otworem (a..b, kaseta b..2b-a)."""
    cut(o, at, a, b, False)
    L = b - a
    if o == 'v':
        doors.append(f'<rect x="{X(at) - 2:g}" y="{Y(b):g}" width="4" height="{L * K:g}" fill="{FLOOR}"/>')
        doors.append(f'<line x1="{X(at):g}" y1="{Y(b - L * 0.3):g}" x2="{X(at):g}" y2="{Y(b + L - 2):g}" stroke="{WALL}" stroke-width="2.5"/>')
    else:
        doors.append(f'<rect x="{X(b):g}" y="{Y(at) - 2:g}" width="{L * K:g}" height="4" fill="{FLOOR}"/>')
        doors.append(f'<line x1="{X(b - L * 0.3):g}" y1="{Y(at):g}" x2="{X(b + L - 2):g}" y2="{Y(at):g}" stroke="{WALL}" stroke-width="2.5"/>')

def window(o, at, a, b):
    cut(o, at, a, b, True)
    d = EXT_W / 2 - 1
    for s in (-d, 0, d):
        if o == 'h':
            win.append(f'<line x1="{X(a):g}" y1="{Y(at) + s:g}" x2="{X(b):g}" y2="{Y(at) + s:g}" stroke="{WIN}" stroke-width="{2 if s else 1.2}"/>')
        else:
            win.append(f'<line x1="{X(at) + s:g}" y1="{Y(a):g}" x2="{X(at) + s:g}" y2="{Y(b):g}" stroke="{WIN}" stroke-width="{2 if s else 1.2}"/>')

for w in [('h',187,235,292), ('h',187,605,705), ('h',577,252,280), ('h',577,347,405), ('h',577,650,677),
          ('h',646,762,820), ('h',646,887,917)]:
    window(*w)
# drzwi tarasowe salonu (przesuwne, przeszklone — część rzutu, nie warstwy drzwi)
cut('h', 577, 495, 610, True)
for s in (-4, 4):
    win.append(f'<line x1="{X(495):g}" y1="{Y(577) + s:g}" x2="{X(555 if s < 0 else 550):g}" y2="{Y(577) + s:g}" stroke="{WIN}" stroke-width="2.5"/>')
    win.append(f'<line x1="{X(550 if s < 0 else 555):g}" y1="{Y(577) - s:g}" x2="{X(610):g}" y2="{Y(577) - s:g}" stroke="{WIN}" stroke-width="2.5"/>')

# każdy wiatrołap: drzwi zewnętrzne (na zewnątrz) + wewnętrzne (do wiatrołapu)
door('h', 485, 187, +1, 46, -1, ext=True)     # wejście główne 01
door('h', 487, 247, +1, 42, -1)               # 01 → 02 (wewnętrzne, do wiatrołapu)
door('h', 440, 350, -1, 35, +1)               # 16 → 19
door('h', 370, 350, -1, 38, -1)               # 19 → 15
door('v', 264, 404, -1, 40, -1, ext=True)     # wejście gościnne 14
door('h', 347, 408, +1, 38, +1)               # 19 → 17
door('v', 789, 316, -1, 38, +1)               # 07 → 08
door('v', 911.5, 316, -1, 38, -1)             # 20 → 07 (do warsztatu — wyjątek: nie zderzają się w wiatrołapie)
door('h', 921, 249, +1, 38, -1)               # 20 → 06 (do tech. suchego — wyjątek jw.)
door('v', 973, 322, -1, 44, +1, ext=True)     # wejście techniczne 20
door('h', 921, 350, +1, 38, -1)               # 05 → 20 (wewnętrzne, do wiatrołapu)
door('h', 965, 408, -1, 36, -1)               # 09 → 05
door('h', 721, 408, +1, 36, +1)               # 05 → 12
cut('h', 350, 487, 530, False)                # 02 → 04 (przejście)
cut('v', 563, 289, 340, False)                # 02 → 03
door('v', 307, 397, -1, 40, -1)              # 19 → 14 (wewnętrzne, do wiatrołapu)
pocket('v', 771, 412, 452)                    # 12 → 10 (przesuwne w kasecie)
sliding('v', 943, 530, 615, 3)                # 12 → 13, trójskrzydłowe

mask = (f'<mask id="m" maskUnits="userSpaceOnUse" x="{VX}" y="{VY}" width="{W}" height="{H}">'
        f'<rect x="{VX}" y="{VY}" width="{W}" height="{H}" fill="white"/>' + ''.join(gaps) + '</mask>')
outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / 'build/www/plan'
outdir.mkdir(parents=True, exist_ok=True)
files = {'rzut_podloga.svg': floor,
         'rzut_sciany.svg': [SVG, mask, '<g mask="url(#m)">'] + walls + ['</g>'] + win,
         'rzut_drzwi.svg': [SVG] + doors}
for name, body in files.items():
    open(f'{outdir}/{name}', 'w').write('\n'.join(body + ['</svg>']) + '\n')

# dane dla panelu: nazwa, punkt etykiety i obrys pokoju w % rzutu
pc = lambda x, y: (round((X(x) - VX) / W * 100, 2), round((Y(y) - VY) / H * 100, 2))
rooms = {f'{n:02d}': {'name': names[n], 'at': pc(*lp),
                      'clip': ', '.join('%g%% %g%%' % pc(x, y) for x, y in poly)}
         for n, (lp, poly) in PLAN.items()}
json.dump(rooms, open(f'{outdir}/rooms.json', 'w'), ensure_ascii=False, indent=1)
print(W, H, len(rooms))
