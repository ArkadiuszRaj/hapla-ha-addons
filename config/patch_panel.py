"""Jednorazowa przeróbka makiety ciemnego panelu (Ciemny.dc.html w artefakcie „Panel ścienny HAPLA”).

Zamienia pojedynczy obraz rzutu na warstwy z gen_plan.py (podłoga, oświetlenie, ściany, drzwi),
dodaje przełączniki Drzwi/Nazwy oraz etykiety temperatura · wilgotność z rooms.json.
Działa tylko na wersji makiety sprzed tej zmiany — kotwice tekstowe znikają po pierwszym
uruchomieniu. Adresy /_blob/… to pliki SVG wgrane do artefaktu; wartości pokoi są przykładowe.

    python3 patch_panel.py <Ciemny.dc.html> <rooms.json>
"""
import json
import sys
f = sys.argv[1]; s = open(f).read()
R = json.load(open(sys.argv[2]))
old_plan=s[s.index('          <sc-if value="{{planOn}}"'):s.index('          <div style="display: flex; gap: 10px;">')]
new_plan='''          <sc-if value="{{planOn}}" hint-placeholder-val="{{false}}">
            <div style="position: relative; border-radius: 18px; background: #0f0f15; padding: 6px;">
              <div role="img" aria-label="Rzut parteru z temperaturą, wilgotnością i oświetleniem pomieszczeń" style="position: relative; aspect-ratio: 1344 / 822;">
                <img src="/_blob/48efee0f422a57f51dfb3d36685fc9c7" alt="" style="position: absolute; inset: 0; width: 100%; height: 100%;">
                <sc-for list="{{lights}}" as="lt" hint-placeholder-count="4">
                  <div style="{{lt.style}}"></div>
                </sc-for>
                <img src="/_blob/8cbd7cf26269a52063e2f8f9aa63905f" alt="" style="position: absolute; inset: 0; width: 100%; height: 100%;">
                <img src="/_blob/a20d3adaecb85e2f75249a2b922be862" alt="" style="{{doorsStyle}}">
                <sc-for list="{{labels}}" as="l" hint-placeholder-count="18">
                  <div style="{{l.pos}}">
                    <div style="{{l.nameStyle}}">{{l.name}}</div>
                    <div style="{{l.valStyle}}">{{l.t}}</div>
                  </div>
                </sc-for>
              </div>
              <div style="position: absolute; right: 14px; bottom: 12px; display: flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; background: rgba(11, 11, 16, 0.72); font-size: 11px; color: #c9c9d6;">
                <span style="width: 10px; height: 10px; border-radius: 3px; background: rgba(255, 200, 97, 0.6);"></span>
                światło
              </div>
            </div>
          </sc-if>
'''
s=s.replace(old_plan,new_plan)
anchor='            <div role="group" aria-label="Widok"'
toggles='''            <sc-if value="{{planOn}}" hint-placeholder-val="{{false}}">
              <div role="group" aria-label="Warstwy rzutu" style="display: flex; gap: 6px;">
                <button onClick="{{toggleDoors}}" aria-pressed="{{doorsOn}}" style="{{doorsBtn}}">Drzwi</button>
                <button onClick="{{toggleNames}}" aria-pressed="{{namesOn}}" style="{{namesBtn}}">Nazwy</button>
              </div>
            </sc-if>
'''
s=s.replace(anchor,toggles+anchor,1)
s=s.replace("this.state = { program: 'Impreza', offset: 0, room: null, view: 'cam' };",
            "this.state = { program: 'Impreza', offset: 0, room: null, view: 'cam', doors: true, names: true };")
vals={'01':('18,4',48,0),'02':(None,None,0),'03':(None,None,1),'04':('21,6',44,.8),'05':('20,9',46,0),
      '06':('17,2',41,0),'07':('16,8',52,0),'08':('19,0',58,0),'09':('20,1',63,0),'10':('22,8',67,0),
      '12':('20,4',47,0),'13':(None,None,0),'14':('18,1',50,0),'15':('21,2',43,.6),'16':('22,1',61,0),
      '17':('19,6',45,0),'19':(None,None,.35),'20':('16,5',49,0)}
rows=',\n    '.join("['%s', '%s', %g, %g, '%s', %s, %s, %g]"%(k,v['name'],v['at'][0],v['at'][1],v['clip'],
     "'%s'"%vals[k][0] if vals[k][0] else 'null', vals[k][1] if vals[k][1] else 'null', vals[k][2]) for k,v in R.items())
a=s.index('  const rooms = ['); b=s.index('  const fav = [')
new_js='''  // [nr, nazwa, etykieta x, y (% rzutu), obrys (clip-path), temp °C, wilgotność %, światło 0..1] — wartości przykładowe
  const rooms = [
    @ROWS@
  ];
  const favNo = { Salon: '04', Sypialnia: '12', Pracownia: '15' };
  const selNo = favNo[s.room];
  const labels = rooms.map(function (r) {
    const on = r[0] === selNo;
    const hasVal = r[5] !== null;
    const show = hasVal || s.names;
    return {
      name: r[1],
      t: hasVal ? r[5] + '° · ' + r[6] + '%' : '',
      nameStyle: s.names ? 'font-size: 10px; font-weight: 600; color: ' + (on ? '#e4e0ff' : '#a1a1b5') + ';' : 'display: none;',
      valStyle: hasVal ? 'font-size: 12px; font-weight: 700;' : 'display: none;',
      pos: (show ? '' : 'display: none; ') +
        'position: absolute; left: ' + r[2] + '%; top: ' + r[3] + '%; transform: translate(-50%, -50%); text-align: center; line-height: 1.25; padding: 3px 7px; border-radius: 8px; white-space: nowrap; ' +
        (on ? 'background: #6d5ce8; color: #ffffff;' : 'background: rgba(20, 20, 28, 0.88); color: #f2f2f7; border: 1px solid #2f2f3a;')
    };
  });
  const lights = rooms.filter(function (r) { return r[7] > 0; }).map(function (r) {
    return { style: 'position: absolute; inset: 0; clip-path: polygon(' + r[4] + '); background: rgba(255, 200, 97, ' + (0.1 + 0.28 * r[7]).toFixed(2) + ');' };
  });
'''.replace('@ROWS@', rows)
s=s[:a]+new_js+s[b:]
s=s.replace("    labels: labels,\n","""    labels: labels,
    lights: lights,
    doorsOn: s.doors,
    namesOn: s.names,
    doorsStyle: 'position: absolute; inset: 0; width: 100%; height: 100%;' + (s.doors ? '' : ' display: none;'),
    doorsBtn: chip(s.doors),
    namesBtn: chip(s.names),
    toggleDoors: function () { self.setState({ doors: !s.doors }); },
    toggleNames: function () { self.setState({ names: !s.names }); },
""")
s=s.replace("  const camOn = s.view === 'cam';\n","""  const camOn = s.view === 'cam';
  const chip = function (on) {
    return 'border-radius: 999px; padding: 0 12px; height: 36px; font: inherit; font-size: 13px; font-weight: 600; ' +
      (on ? 'border: 1px solid #6d5ce8; background: #211e38; color: #e4e0ff;' : 'border: 1px solid #2f2f3a; background: transparent; color: #8d8da0;');
  };
""")
open(f,'w').write(s)
