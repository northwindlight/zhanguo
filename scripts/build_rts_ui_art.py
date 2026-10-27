"""Original pixel SVGs for tile construction cards and distinct world facilities."""
from pathlib import Path
import json,hashlib
ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'mods/ui/desktop/assets/rts-ui-v1'


def svg(body,w=24,h=24):return f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" shape-rendering="crispEdges">{body}</svg>\n'
def rect(x,y,w,h,c):return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#{c}"/>'
def poly(points,c):return f'<polygon points="{points}" fill="#{c}"/>'


def build():
 DEST.mkdir(parents=True,exist_ok=True);art={}
 art['castle']=svg(''.join([
 rect(5,44,38,7,'544e43'),rect(7,38,34,10,'777d76'),rect(11,23,26,23,'adb3a0'),rect(13,25,22,3,'d5d4b8'),
 rect(3,17,11,29,'929d94'),rect(34,17,11,29,'929d94'),rect(2,14,13,8,'c5c7b1'),rect(33,14,13,8,'c5c7b1'),
 *[rect(x,10,3,6,'b6bda9') for x in (2,7,12,33,38,43)],rect(6,24,3,7,'354744'),rect(39,24,3,7,'354744'),
 rect(18,12,12,26,'83998f'),poly('16,13 24,4 32,13','765651'),rect(23,0,1,6,'534c3d'),rect(24,0,7,3,'d7af58'),
 rect(21,18,6,7,'334746'),rect(18,35,12,13,'4d4334'),poly('18,35 24,31 30,35','4d4334'),rect(20,36,8,12,'71634c'),
 rect(23,35,2,14,'353f38'),*[rect(x,y,4,1,'78867e') for x,y in ((14,30),(29,31),(7,35),(37,39),(13,41),(31,37))],
 rect(16,48,16,3,'c9c0a0'),rect(14,51,20,2,'928c77')]),48,56)
 art['lumberyard']=svg(''.join([
 rect(3,45,42,6,'6f7850'),rect(3,49,42,3,'4a6048'),
 rect(7,24,23,22,'9b754a'),rect(10,27,18,17,'bd995f'),poly('3,25 18,11 34,25','496b59'),poly('7,23 18,14 30,23','64836a'),
 rect(15,29,8,15,'514d35'),rect(7,43,25,3,'6d5a40'),
 rect(30,32,14,4,'8f653e'),rect(29,37,15,4,'8f653e'),rect(28,42,16,4,'8f653e'),
 *[rect(x,y,4,4,'d8b77c') for x,y in ((39,32),(39,37),(39,42))],
 rect(28,18,2,18,'374c40'),poly('20,23 29,6 38,23','385c46'),poly('22,17 29,1 36,17','59805a'),
 rect(2,34,5,8,'795938'),rect(1,33,7,3,'dfbe86'),rect(2,38,5,1,'a48355'),
 rect(33,46,11,2,'bec7b1'),rect(36,45,1,4,'a5ad96')]),48,56)
 art['gold']=svg(poly('3,8 7,4 18,4 22,8 22,17 18,21 7,21 3,17','8f6335')+rect(5,8,15,9,'dcaf46')+rect(7,5,10,3,'f3d47e')+rect(9,9,4,7,'b48132'))
 art['wood']=svg(poly('3,9 18,4 22,8 8,14','c09659')+poly('3,9 8,14 8,21 3,17','93643e')+poly('8,14 22,8 22,15 8,21','9e713f')+rect(4,10,3,6,'dfb478')+rect(5,12,1,3,'8d6038'))
 art['food']=svg(rect(11,3,2,19,'c4a66a')+''.join(poly(p,'dfc476') for p in ('11,8 6,3 5,7 11,12','13,6 18,1 19,5 13,10','11,14 5,9 4,13 11,18','13,12 19,7 20,11 13,16','13,18 19,13 20,17 13,21')))
 art['ore']=svg(poly('2,16 7,6 15,3 22,12 19,21 7,21','637677')+poly('7,6 15,3 13,12 2,16','a8b9b0')+poly('13,12 22,12 19,21 9,20','84948f'))
 art['oil']=svg(poly('12,2 4,13 4,18 8,22 16,22 20,18 20,13','273f43')+poly('12,5 8,13 8,18 11,20 13,18 10,16 10,12','66898a'))
 art['equipment']=svg(rect(2,6,20,14,'827853')+rect(4,8,16,10,'a79969')+rect(8,5,3,16,'5c675b')+poly('14,2 21,2 20,7 16,12 14,10','c7d3c2')+poly('16,10 18,12 9,22 7,20','697e79'))
 art['supply']=svg(rect(3,7,18,14,'8d8056')+rect(5,9,14,10,'a6a277')+rect(10,4,4,17,'d3ccb0')+rect(7,10,10,3,'6c8c71'))
 art['energy']=svg(poly('13,1 4,14 11,14 9,23 21,9 14,9 17,1','e5c776'))
 art['farmland']=svg(rect(2,6,20,16,'806b48')+''.join(rect(4,y,16,2,'b8a16a') for y in (8,13,18))+rect(5,2,2,7,'729169')+rect(16,3,2,6,'8aab72'))
 art['resource']=svg(rect(3,5,18,16,'675d43')+rect(5,7,14,12,'b29a68')+rect(3,11,18,2,'77664a')+rect(10,5,3,16,'d0ba86'))
 factory=(ROOT/'mods/ui/desktop/assets/unified-v1/svg/factory.svg').read_text()
 base=factory[factory.index('>')+1:factory.rindex('</svg>')]
 for name,resource,tone in [('woodpower','wood','876a4b'),('oilpower','oil','596f7a'),('supplyfactory','supply','78895a'),('equipmentfactory','equipment','657e8d')]:
  body=base.replace('#4b6971','#'+tone).replace('#8d9078','#'+tone)
  badge=art[resource];badge=badge[badge.index('>')+1:badge.rindex('</svg>')]
  body+=rect(25,28,16,16,'334745')+f'<g transform="translate(27 30) scale(0.5)">{badge}</g>'
  if name.endswith('power'):
   body+=poly('17,8 11,17 16,17 14,23 23,13 18,13 20,8','dfc171')
  art[name]=svg(body,64,64)
 for name,body in art.items():(DEST/(name+'.svg')).write_text(body)
 manifest={'format':'zhanguo.rts-art/1','author':'Codex','license':'MPL-2.0','assets':[{'id':name,'path':name+'.svg','sha256':hashlib.sha256(body.encode()).hexdigest()} for name,body in art.items()]}
 (DEST/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':build()
