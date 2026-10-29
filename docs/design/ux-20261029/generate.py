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
        stocks=[('金','900'),('粮','25'),('木','58'),('矿','10'),('油','0'),('装','5'),('补','9'),('电','2/1')]
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
    s.rect(610,300,120,120,'none','#edd48e',4)
    s.icon('house',670,347,1.2);s.text(670,401,'瀚与',17,anchor='middle')
    x,w=1072,352;s.panel(x,72,w,756)
    s.button(x+12,84,158,'▧ 地块',True,h=36);s.button(x+182,84,158,'⚑ 军队',h=36)
    s.text(x+16,151,'瀚与  (3,11)',21);s.text(x+16,178,'本国 · 平原',15,'muted')
    s.rect(x+14,194,w-28,60,'#494837',r=4)
    s.text(x+26,218,'本期已施工',17,'gold');s.text(x+26,241,'已建2  ·  施工1  ·  待建1',15)
    s.text(x+16,281,'本地建筑',16,'muted')
    s.icon('house',x+38,309,.5);s.text(x+38,333,'市政厅',14,anchor='middle')
    s.icon('camp',x+148,309,.5);s.text(x+148,333,'兵营',14,anchor='middle')
    s.icon('farm',x+258,309,.5);s.text(x+258,333,'农场·在建',14,'gold','middle')
    s.text(x+16,352,'耕地剩余1/2  ·  驻军1支',15,'muted')
    s.line(x+14,367,x+w-14,367)
    s.button(x+14,378,100,'建造',True,h=36);s.button(x+126,378,100,'征募',h=36);s.button(x+238,378,100,'管理',h=36)
    s.text(x+16,443,'生产  /  军备  /  城市',15,'muted')
    cards=[('农场','20金 15木','farm',True),('林场','20金 15木','tree',False),('矿场','缺矿点','house',False)]
    for i,(label,cost,icon,selected) in enumerate(cards):
        cx=x+14+i*112;s.rect(cx,459,100,106,'#375247' if selected else '#263e39','#e5ce91' if selected else '#486455',4)
        s.icon(icon,cx+50,489,.55);s.text(cx+50,526,label,16,anchor='middle');s.text(cx+50,551,cost,13,'gold' if selected else 'muted','middle')
    s.button(x+14,579,324,'建造农场 · 本期额度已用',h=42)
    s.button(x+14,630,324,'◷ 安排后续生产…',h=36)
    s.line(x+14,681,x+w-14,681)
    s.text(x+16,709,'本地生产  施工1 · 等待1',17)
    s.text(x+16,739,'农场 · 本期末完工',15);s.text(x+16,760,'撤销施工返还50%',13,'muted')
    s.text(x+178,739,'瞭望塔 · 等待开工',15);s.text(x+178,760,'未开工取消全退',13,'muted')
    s.text(x+16,807,'◷ 全国生产',15,'cyan')
    s.panel(424,650,454,112)
    s.text(444,681,'点地块先看“有什么、做过没有”',19,'gold')
    s.text(444,710,'主操作看本期资格；后续排程有独立入口。',16)
    s.text(444,739,'状态留在地块上，不靠回执或长悬浮寻找。',16,'muted')
    s.finish('gui-ux-land-20261029.svg')


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
    x,w=688,320;s.panel(x,104,w,592)
    s.button(x+12,116,142,'⚑ 军队',True,h=36);s.button(x+166,116,142,'▧ 地块',h=36)
    s.button(x+12,164,142,'部队',True,h=34);s.button(x+166,164,142,'征募地点',h=34)
    s.rect(x+12,211,w-24,54,'#314d43',r=4)
    s.text(x+24,235,'已选2支 / 2处驻地',17,'cyan');s.text(x+24,255,'查看全部编号   ·   清空选择',13,'muted')
    for i,(name,hp,ratio) in enumerate((('#1 民兵 · 瀚与','38/80',.475),('#2 步兵 · 延牧','100/100',1))):
        y=279+i*64;s.text(x+18,y+17,name,16);s.text(x+w-18,y+17,hp,14,'muted','end')
        s.rect(x+18,y+29,w-36,6,'#172725',r=2);s.rect(x+18,y+29,(w-36)*ratio,6,'#8fc79a',r=2)
    s.text(x+16,431,'新目标  (6,5)',18,'gold');s.text(x+16,456,'收到回执后更新路线与下一步',14,'muted')
    s.text(x+16,490,'→ ①  (6,5)  移动',16);s.text(x+16,520,'＋ 按住Shift追加下一个节点',14,'muted')
    s.line(x+14,537,x+w-14,537)
    s.text(x+16,566,'☑ 低于30生命暂停',16);s.text(x+16,589,'正常补给恢复满血后继续',14,'muted')
    s.button(x+14,607,92,'→ 移动',h=38);s.button(x+114,607,92,'⚔ 攻击',h=38);s.button(x+214,607,92,'■ 停止',h=38)
    s.text(x+16,675,'右键移动 · Ctrl攻击 · Shift追加',13,'muted')
    s.panel(40,613,598,76)
    s.icon('camp',72,647,.6)
    s.text(102,641,'征募地点页：直接列兵营和军屯格子',18,'gold')
    s.text(102,668,'地点 → 定位与征募，不必挨个地块找设施。',15,'muted')
    s.finish('gui-ux-army-20261029.svg')


def recruitment():
    s=Sketch(1024,768);s.map(True);s.header(True)
    s.text(18,111,'方向原型 · 非运行截图 · 数值仅示例',13,'muted')
    s.rect(254,308,104,104,'none','#edd48e',4)
    s.icon('house',306,350,1.1);s.text(306,397,'瀚与 · 兵营',16,anchor='middle')
    x,w=688,320;s.panel(x,104,w,592)
    s.button(x+12,116,142,'⚑ 军队',True,h=36);s.button(x+166,116,142,'▧ 地块',h=36)
    s.button(x+12,164,142,'部队',h=34);s.button(x+166,164,142,'征募地点',True,h=34)
    s.text(x+16,229,'本国征募地点',19)
    s.text(x+16,254,'位置、设施和施工状态一起展示',14,'muted')
    for i,(place,facility,status,ready) in enumerate((('瀚与 (3,11)','兵营','已落成',True),('延牧 (2,11)','军屯','已落成',True),('明柚 (4,11)','兵营','施工中',False))):
        y=270+i*126;s.rect(x+12,y,w-24,114,'#263e39','#486455',4)
        s.icon('camp' if facility=='军屯' else 'house',x+40,y+35,.6)
        s.text(x+66,y+31,place,17);s.text(x+66,y+56,facility+' · '+status,15,'cyan' if ready else 'gold')
        s.button(x+24,y+67,98,'定位',h=34)
        s.button(x+134,y+67,150,'征募…' if ready else '等待落成',ready,h=34)
    s.text(x+16,675,'点地点后读取正式征募条件',14,'muted')
    s.panel(76,565,516,105)
    s.text(96,596,'军队面板也能找到“在哪里征兵”',19,'gold')
    s.text(96,627,'没有部队时，征募地点页仍然可用。',16)
    s.text(96,652,'地图定位与地块属性共用同一入口。',15,'muted')
    s.finish('gui-ux-recruitment-20261029.svg')


if __name__=='__main__':land();army();recruitment()
