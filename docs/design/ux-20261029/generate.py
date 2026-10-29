"""Rebuild static native-GUI layout sketches, not game screens or rule data."""
from html import escape
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


class Sketch:
    def __init__(self,width,height):
        self.width,self.height=width,height
        self.parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<title>战国 UI 体验方向草图：非游戏截图，数值仅示例</title>',
            '<style>text{font-family:"Noto Sans CJK SC","Microsoft YaHei",sans-serif;fill:#edf0df} .muted{fill:#b0c2b6} .gold{fill:#e5ce91} .cyan{fill:#8edcc9}</style>',
            '<defs><pattern id="grid" width="120" height="120" patternUnits="userSpaceOnUse"><rect width="120" height="120" fill="#537462"/><path d="M120 0H0V120" fill="none" stroke="#708b72" stroke-opacity=".35"/></pattern>',
            '<g id="house"><path d="M-22 0h44v28h-44z" fill="#c7ae81"/><path d="M-28 0 0-18 28 0z" fill="#6f9490"/><path d="M-5 11h10v17H-5z" fill="#344a43"/></g>',
            '<g id="farm"><path d="M-24-14h48v34h-48z" fill="#b28d52"/><path d="M-18-12v30m9-30v30m9-30v30m9-30v30m9-30v30" stroke="#e2ca7c" stroke-width="3"/></g>',
            '<g id="camp"><path d="m-24 20 24-40 24 40z" fill="#c3b37f"/><path d="m-10 20 10-24 10 24z" fill="#3c5650"/><path d="M0-22v-14h18v10H0" fill="#77c9b7"/></g>',
            '<g id="castle"><path d="M-24 20v-44h10v9h9v-9H5v9h9v-9h10v44z" fill="#a4b4ac"/><path d="M-6 20V2H6v18z" fill="#344a43"/></g>',
            '<g id="tower"><path d="M-13 22v-42h26v42z" fill="#c7ae81"/><path d="m-19-20 19-14 19 14z" fill="#6f9490"/><path d="M-5-12H5V0H-5z" fill="#344a43"/></g>',
            '<g id="plus"><path d="M-9 0H9M0-9V9" fill="none" stroke="#8ba596" stroke-width="2"/></g>',
            '<g id="tree"><path d="M-3 8h6v18h-6z" fill="#8c7958"/><path d="m0-26 18 24H9l16 18h-50L-9-2h-9z" fill="#385e46"/><path d="m0-23 14 20H5L18 9H0z" fill="#6d9369"/></g>',
            '<g id="person"><circle cy="-9" r="4" fill="#cfb690"/><path d="M-5-4H5V8H-5z" fill="#7ed7c0"/><path d="M-3 8v7M3 8v7" stroke="#304b48" stroke-width="3"/></g>',
            '<g id="bank"><path d="m-13-5 13-8 13 8zM-12 12h24" fill="#e5ce91" stroke="#e5ce91" stroke-width="2"/><path d="M-8-2v12M0-2v12M8-2v12" stroke="#d2e1c9" stroke-width="3"/></g>',
            '<g id="speech"><path d="M-12-10h24V8H-3l-7 6V8h-2z" fill="none" stroke="#d2e1c9" stroke-width="2"/><path d="M-7-4H7M-7 2H3" stroke="#d2e1c9" stroke-width="2"/></g>',
            '<g id="mail"><path d="M-13-9h26V10h-26zM-13-9 0 2l13-11" fill="none" stroke="#d2e1c9" stroke-width="2"/></g>',
            '<g id="menu"><path d="M-11-8h22M-11 0h22M-11 8h22" stroke="#d2e1c9" stroke-width="2"/></g></defs>']
        self.rect(0,0,width,height,'#142524')

    def rect(self,x,y,w,h,fill='#203735',stroke=None,r=0):
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}"'+(f' stroke="{stroke}"' if stroke else '')+'/>')

    def text(self,x,y,value,size=16,kind='',anchor='start'):
        self.parts.append(f'<text x="{x}" y="{y}" font-size="{size}" class="{kind}" text-anchor="{anchor}">{escape(value)}</text>')

    def line(self,x,y,x2,y2,stroke='#425b53',width=1,dash=None):
        self.parts.append(f'<path d="M{x} {y}L{x2} {y2}" fill="none" stroke="{stroke}" stroke-width="{width}"'+(f' stroke-dasharray="{dash}"' if dash else '')+'/>')

    def icon(self,name,x,y,scale=.5):
        self.parts.append(f'<use href="#{name}" transform="translate({x} {y}) scale({scale})"/>')

    def button(self,x,y,w,label,active=False,h=40):
        self.rect(x,y,w,h,'#d8c286' if active else '#29423f','#526e60',4)
        if active:
            self.parts.append(f'<text x="{x+w/2}" y="{y+h/2+6}" text-anchor="middle" font-size="16" style="fill:#172e29">{escape(label)}</text>')
        else:self.text(x+w/2,y+h/2+6,label,16,anchor='middle')

    def map(self,compact=False):
        start=88 if compact else 56
        self.rect(0,start,self.width,self.height-start,'#263e39')
        self.rect(48,start+72,self.width-100,self.height-start-172,'url(#grid)')
        for x,y in ((118,195),(172,226),(140,348),(340,426),(510,247),(570,300),(155,595),(450,650),(600,580)):
            self.icon('tree',x,y,.9)
        for x,y in ((280,345),(430,365),(400,530),(590,430)):
            self.icon('house',x,y,.8)
        self.icon('camp',490,550,.8);self.icon('farm',300,535,.9)
        for x,y in ((298,405),(322,414),(480,420),(450,592),(291,565)):
            self.icon('person',x,y)
        self.rect(0,self.height-56,self.width,56,'#142625')

    def header(self,compact=False):
        self.rect(0,0,self.width,88 if compact else 56,'#112321')
        self.text(18,32,'青岚 · 第4回合',18)
        stocks=[('金','900'),('粮','25'),('木','58'),('矿','10'),('油','0'),('装','5'),('补','9'),('电','2/2')]
        for i,(label,amount) in enumerate(stocks):
            x=(18 if compact else 244)+i*(82 if compact else 90)
            y=66 if compact else 32
            self.text(x,y,label,14,'muted');self.text(x+24,y,amount,16,'gold' if i==0 else '')
        x=self.width-220
        self.button(self.width-316,8,80,'Ⅱ 1×',h=36)
        for i,(icon,title) in enumerate((('bank','经济：市场、银行、报表'),('speech','外交：条约、联盟、信件'),('mail','通知与邮箱'),('menu','菜单与设置'))):
            bx=x+i*52
            self.parts.append('<g><title>'+escape(title)+'</title>')
            self.rect(bx,8,44,36,'#29423f','#526e60',4);self.icon(icon,bx+22,26,.7)
            if icon=='mail':
                self.rect(bx+31,9,12,14,'#d8c286',r=3)
                self.parts.append(f'<text x="{bx+37}" y="20" font-size="11" text-anchor="middle" style="fill:#172e29">1</text>')
            self.parts.append('</g>')
        self.text(18,self.height-20,'▧ 地图    ? 指南',16,'muted')
        self.text(self.width-212,self.height-20,'轮到你行动',14,'muted','end')
        self.button(self.width-194,self.height-48,178,'结束本回合 →',True)

    def panel(self,x,y,w,h):
        self.rect(x,y,w,h,'#1c3331','#6a8773',6)

    def finish(self,name):
        self.parts.append('</svg>');(ROOT/name).write_text('\n'.join(self.parts)+'\n',encoding='utf-8')


def land():
    s=Sketch(1440,900);s.map();s.header()
    s.text(20,82,'方向原型 · 非运行截图 · 图形与数值仅示例',13,'muted')
    s.rect(160,300,120,120,'none','#edd48e',4)
    s.icon('house',220,347,1.2);s.text(220,401,'瀚与',17,anchor='middle')
    s.text(42,478,'选中一块地',20,'gold')
    s.text(42,508,'查看、建设、管理',18)
    s.text(42,537,'都在它自己的面板里。',16,'muted')
    # One object panel, not a separate global building page or stacked drawers.
    s.panel(348,104,1068,720)
    s.text(368,144,'瀚与  (3,11)',27)
    s.text(368,173,'青岚领土 · 本国实时信息',15,'muted')
    s.button(1068,124,84,'← 上一格',h=36)
    s.button(1160,124,84,'下一格 →',h=36)
    s.button(1252,124,84,'定位',h=36)
    s.button(1344,124,52,'×',h=36)
    # These are explicit full-load examples, not site-level actual production.
    s.rect(368,192,248,104,'#263e39',r=4)
    s.text(384,216,'森林 · 建设金币 +15%',16,'gold')
    s.text(384,243,'地形减伤10% · 城堡10%',15)
    s.text(384,272,'格主守军合成减伤 19%',16,'cyan')
    s.rect(632,192,424,104,'#263e39',r=4)
    s.text(648,216,'本地生产 · 满负荷示意',16,'muted')
    s.text(648,248,'+8 金   +1 粮',23,'cyan')
    s.text(888,248,'耗电 2',18,'gold')
    s.text(648,276,'假定全国供电与原料充足；非实际入库',15,'muted')
    s.rect(1072,192,324,104,'#494837',r=4)
    s.text(1088,219,'本期已动工 1 / 1',19,'gold')
    s.text(1088,246,'已落成4 · 施工1 · 待建1',16)
    s.text(1088,275,'建筑位占用 6 / 20（含待建）',15,'muted')
    s.button(368,310,168,'建筑与建设',True,h=36)
    s.button(546,310,168,'产出明细',h=36)
    s.button(724,310,168,'驻军与征募',h=36)
    s.line(368,358,1396,358)
    s.text(368,388,'本地建筑',19)
    s.text(500,388,'耕地空位 1 / 3',15,'muted')
    s.button(868,368,168,'＋ 建造…',h=34)
    s.line(1056,376,1056,796)
    s.text(1076,390,'施工与待建  2',19)
    buildings=[('house','市政厅'),('camp','兵营'),('farm','农场'),('castle','城堡 I'),
               ('farm','农场 · 在建'),('tower','瞭望塔 · 待建')]
    for i in range(20):
        cx=368+(i%5)*136;cy=406+(i//5)*72
        selected=i==4;pending=i==5
        s.rect(cx,cy,124,64,'#465040' if selected else '#233d36' if i<4 else '#1b302c',
               '#e5ce91' if selected else '#738876' if pending else '#3e5b4d',4)
        if i<len(buildings):
            icon,title=buildings[i]
            s.parts.append('<g><title>'+escape(title)+'</title>')
            s.icon(icon,cx+62,cy+21,.52)
            s.text(cx+62,cy+53,title,14,'gold' if selected else 'muted' if pending else '',anchor='middle')
            if pending:s.line(cx+12,cy+60,cx+112,cy+60,'#a0b09a',dash='4 4')
            s.parts.append('</g>')
        else:
            s.parts.append('<g><title>空建筑位：打开本地建造清单；施工额度与资源位以正式条件为准</title>')
            s.icon('plus',cx+62,cy+31,.8);s.parts.append('</g>')
    s.text(368,710,'点空位或建造图标，在本面板展开可建项目与条件。',16,'muted')
    s.rect(368,730,668,70,'#263e39',r=4)
    s.icon('farm',397,760,.6)
    s.text(426,754,'农场 · 施工中',17,'gold')
    s.text(426,779,'占耕地1 · 完工后满负荷产粮 +1 / 回合',15)
    # Queue stays local and visible while browsing the object's sections.
    s.rect(1072,406,324,144,'#364737','#6c8068',4)
    s.text(1088,435,'农场 · 正在施工',18,'gold')
    s.text(1088,465,'本回合末落成',16)
    s.text(1088,490,'下回合开始参与生产',15,'muted')
    s.button(1088,507,292,'撤销施工 · 返还50%',h=32)
    s.rect(1072,564,324,144,'#263e39','#486455',4)
    s.text(1088,593,'① 瞭望塔 · 待建',18)
    s.text(1088,623,'等待下次施工额度',16,'muted')
    s.text(1088,648,'已预付 69金 / 30木',15,'gold')
    s.button(1088,665,292,'取消待建 · 全额退回',h=32)
    s.text(1076,741,'施工农场已付 23金 / 15木',14,'muted')
    s.text(1076,765,'撤销后本期施工额度仍已使用',14,'muted')
    s.text(1076,793,'◷ 查看全国生产汇总',15,'cyan')
    s.finish('gui-ux-land-20261029.svg')


def military_panel(s):
    x,w=688,320;s.panel(x,104,w,592)
    s.button(x+12,116,142,'⚑ 军队',True,h=36);s.button(x+166,116,142,'▧ 地块',h=36)
    s.rect(x+12,164,w-24,62,'#314d43',r=4)
    s.text(x+24,188,'已选2支 / 2处驻地',17,'cyan')
    s.text(x+24,211,'#1、#2  ·  查看全部  ·  清空',14,'muted')


def military_group(s,y,title,summary,expanded=False):
    s.parts.append('<g><title>'+escape(title+'：'+('已展开，可独立收起' if expanded else '已收起，可独立展开'))+'</title>')
    s.rect(700,y,296,34,'#3d5043' if expanded else '#29423b','#526e60',4)
    s.text(712,y+23,('▾ ' if expanded else '▸ ')+title,16,'gold' if expanded else '')
    s.text(984,y+23,summary,13,'muted','end')
    s.parts.append('</g>')


def army():
    s=Sketch(1024,768);s.map(True);s.header(True)
    s.text(18,111,'方向原型 · 非运行截图 · 数值仅示例',13,'muted')
    s.line(300,500,500,350,'#a6d6c4',3)
    s.rect(468,312,63,63,'none','#e7d293',4)
    for x,y in ((290,486),(307,495),(320,480)):s.icon('person',x,y,.85)
    s.text(332,485,'①',22,'gold');s.text(517,335,'新目的地',16,'gold')
    s.panel(126,166,456,76)
    s.text(145,195,'重新指定目标，替换剩余路线',20,'gold')
    s.text(145,224,'普通新目标＝重设  ·  Shift＝追加（候选映射）',15,'muted')
    military_panel(s)
    military_group(s,238,'正规军','1支 · 已选1',True)
    military_group(s,356,'民兵','1支 · 已选1',True)
    for y,name,hp,ratio in ((280,'☑ #2 步兵 · 延牧','100/100',1),(398,'☑ #1 民兵 · 瀚与','38/80',.475)):
        s.rect(700,y,296,66,'#233d36',r=4)
        s.text(710,y+22,name,16);s.text(986,y+22,hp,14,'muted','end')
        s.rect(710,y+32,276,5,'#172725',r=2);s.rect(710,y+32,276*ratio,5,'#8fc79a',r=2)
        s.text(710,y+56,'可行动 · 路线1节点',14,'cyan')
    military_group(s,476,'兵营（含军屯）','3处')
    military_group(s,522,'要塞和市政厅','3处')
    s.text(704,585,'新目标 (6,5) · 回执后更新',16,'gold')
    s.text(704,610,'☑ 低于30生命暂停，满血继续',14,'muted')
    s.button(702,623,92,'→ 移动',h=36);s.button(802,623,92,'⚔ 攻击',h=36);s.button(902,623,92,'■ 停止',h=36)
    s.text(704,681,'右键移动 · Ctrl攻击 · Shift追加',13,'muted')
    s.panel(40,613,598,76)
    s.icon('camp',72,647,.6)
    s.text(102,641,'四组独立展开／收起，已选军队始终可查',18,'gold')
    s.text(102,668,'展开兵营找征募地点；展开据点找要塞和市政厅。',15,'muted')
    s.finish('gui-ux-army-20261029.svg')


def recruitment():
    s=Sketch(1024,768);s.map(True);s.header(True)
    s.text(18,111,'方向原型 · 非运行截图 · 数值仅示例',13,'muted')
    s.rect(254,308,104,104,'none','#edd48e',4)
    s.icon('house',306,350,1.1);s.text(306,397,'瀚与 · 兵营',16,anchor='middle')
    military_panel(s)
    military_group(s,238,'正规军','1支 · 已选1')
    military_group(s,282,'民兵','1支 · 已选1')
    military_group(s,326,'兵营（含军屯）','3处',True)
    for i,(place,facility,status,ready) in enumerate((('瀚与 (3,11)','兵营','已落成',True),('延牧 (2,11)','军屯','已落成',True),('明柚 (4,11)','兵营','施工中',False))):
        y=370+i*80;s.rect(700,y,296,72,'#263e39','#486455',4)
        s.icon('camp' if facility=='军屯' else 'house',724,y+30,.5)
        s.text(748,y+25,place,16);s.text(748,y+52,facility+' · '+status,14,'cyan' if ready else 'gold')
        s.button(924,y+18,60,'征募…' if ready else '查看',h=34)
    military_group(s,620,'要塞和市政厅','3处')
    s.text(704,681,'点条目定位，进入同一地块面板',14,'muted')
    s.panel(76,565,516,105)
    s.text(96,596,'兵营组列出本国兵营与军屯所在格',19,'gold')
    s.text(96,627,'收起正规军／民兵，仍能知道哪些部队已选。',16)
    s.text(96,652,'点设施进入地块面板，读取正式征募条件。',15,'muted')
    s.finish('gui-ux-recruitment-20261029.svg')


if __name__=='__main__':land();army();recruitment()
