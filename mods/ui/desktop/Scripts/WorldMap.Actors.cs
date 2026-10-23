using Godot;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Zhanguo.Desktop.Layout;

public partial class WorldMap
{
    private JsonObject? _beat;
    private float _beatProgress;
    private Vector2? _cameraTarget;
    private readonly Dictionary<RegionPlan,(Vector2 A,Vector2 B)[]> _walks=new();
    private readonly Dictionary<(int,int),Vector2> _guardAnchors=new();
    public int Residents=>_layout?.Regions.Sum(r=>r.People.Count)??0;
    public int Guards=>Frame?["scene"]?["guards"]?.AsArray().Count??0;
    public int RegionNodes=>_artwork.RegionNodeCount;
    public float PoliticalOpacity=>DesktopScene.NationOpacity(_scale);
    public int VisibleResidents { get; private set; }
    public int VisibleSoldiers { get; private set; }
    public bool ShowGuards=true;

    public void BeginBeat(JsonObject beat,bool follow)
    {
        _beat=beat;_beatProgress=0;
        DesktopScene.Apply(Frame!,beat["delta"]!.AsObject());SetFrame(Frame!);
        if(follow&&beat["position"] is JsonArray p)
        {
            _fit=false;_scale=.88f;
            _cameraTarget=Size*.5f-(new Vector2(I(p[0]),I(p[1]))+Vector2.One*.5f)*256*_scale;
            UpdateView();
        }
    }
    public void BeatProgress(float progress) { _beatProgress=progress;QueueRedraw(); }
    public void EndBeat() { _beat=null;_beatProgress=0;QueueRedraw(); }
    private Vector2 Screen(Vector2 world)=>_offset+world*_scale;
    private bool OnScreen(Vector2 p,float margin=40)=>p.X>=-margin&&p.Y>=-margin&&p.X<=Size.X+margin&&p.Y<=Size.Y+margin;

    private bool WalkSafe(RegionPlan r,Vector2 p)
    {
        var body=new Footprint(p.X-3,p.Y-8,6,8);
        return _layout!.CanBuild(r.Input,body)&&!r.Structures.Any(b=>b.Bounds.Overlaps(body,3))&&
            !r.Trees.Any(t=>new Footprint(t.Foot.X-7,t.Foot.Y-15,14,15).Overlaps(body,1))&&
            !r.Rocks.Any(t=>new Footprint(t.X-7,t.Y-12,14,12).Overlaps(body,1));
    }
    private (Vector2 A,Vector2 B)[] Walks(RegionPlan r)
    {
        if(_walks.TryGetValue(r,out var old))return old;
        var paths=r.People.Select((p,i)=>
        {
            var a=new Vector2(p.X,p.Y);
            var b=a+new Vector2((i%2==0?1:-1)*(12+i%3*4),(i%3-1)*10);
            for(int n=1;n<=8;n++)if(!WalkSafe(r,a.Lerp(b,n/8f))){b=a;break;}
            return (a,b);
        }).ToArray();
        _walks[r]=paths;return paths;
    }
    private void MiniPerson(Vector2 foot,Color cloth,float size,double clock,bool moving=false,bool soldier=false,bool horse=false,bool working=false,float alpha=1,bool fighting=false,bool wild=false,bool facingRight=true)
    {
        int step=moving?(int)(clock*7)%2:0;
        Vector2 p=foot-new Vector2(3,8)*size;
        Color C(string value)=>new Color(new Color(value),alpha);
        void Box(float x,float y,float w,float h,Color c)=>DrawRect(new Rect2(p+new Vector2(x,y)*size,new Vector2(w,h)*size),c);
        DrawCircle(foot+new Vector2(0,1)*size,3.2f*size,new Color(0,0,0,.16f*alpha));
        if(horse)
        {
            Box(-2,5,10,4,C("856347"));Box(facingRight?6:-3,2,3,5,C("a4845e"));
            Box(-1,8,1,3-step,C("403d36"));Box(5,8,1,2+step,C("403d36"));p.Y-=2*size;
        }
        Box(1,1,4,3,C("e5c193"));Box(1,4,4,3,new Color(cloth,alpha));
        Box(1,7,1,2+step,C("3d4844"));Box(4,7,1,3-step,C("3d4844"));
        if(soldier)
        {
            Box(0,0,6,2,C(wild?"7e6249":"b4c2bb"));
            float reach=fighting?(float)Math.Sin(clock*14)*3:0;
            DrawLine(p+new Vector2(facingRight?5:1,5)*size,p+new Vector2(facingRight?8+reach:-2-reach,2)*size,C(wild?"a68c67":"e7e5cd"),Math.Max(1,size));
            Box(facingRight?-1:5,4,2,4,new Color(cloth.Darkened(.28f),alpha));
        }
        else
        {
            Box(0,0,6,1,C("9c8056"));
            if(moving)Box(4,4,4,3,C("bba77d"));
            if(working)
            {
                float swing=(float)Math.Sin(clock*11)*3;
                DrawLine(p+new Vector2(5,5)*size,p+new Vector2(9,1+swing)*size,C("d5c6a0"),Math.Max(1,size));
            }
        }
    }
    private void DrawResidents()
    {
        VisibleResidents=0;
        if(_layout==null)return;
        foreach(var r in _layout.Regions)
        {
            var origin=new Vector2(r.Input.X*256,r.Input.Y*256);
            if(!new Rect2(_offset+origin*_scale,Vector2.One*256*_scale).Grow(30).Intersects(new Rect2(Vector2.Zero,Size)))continue;
            var walks=Walks(r);
            for(int i=0;i<walks.Length;i++)
            {
                if(_scale<.3f&&i%5!=0)continue;
                float t=(float)((_time/9+i*.193+r.Input.X*.13)%1);
                float travel=t<.4f?t/.4f:t<.55f?1:t<.95f?1-(t-.55f)/.4f:0;
                var p=Screen(origin+walks[i].A.Lerp(walks[i].B,travel));if(!OnScreen(p))continue;
                bool moving=walks[i].A!=walks[i].B&&(t<.4f||t is >.55f and <.95f);
                MiniPerson(p,new Color(i%3==0?"b59a67":i%3==1?"728c8a":"9b7765"),Math.Max(.32f,_scale),_time+i,moving);VisibleResidents++;
            }
            foreach(var b in r.Structures.Where(b=>b.Remaining>0))
                for(int i=0;i<3;i++)
                {
                    var at=origin+new Vector2(b.Bounds.X-6+i*10,b.Bounds.Y+b.Bounds.Height+6);
                    if(_layout.Water(at.X,at.Y)>.15f)continue;
                    var p=Screen(at);if(!OnScreen(p))continue;
                    MiniPerson(p,new Color("c9ab72"),_scale,_time+i,working:true);VisibleResidents++;
                    if((int)(_time*5+i)%5==0)DrawCircle(p+new Vector2(4,-6)*_scale,Math.Max(1,_scale),new Color("d6c18e"));
                }
        }
    }
    private Vector2 GuardAnchor(JsonNode u)
    {
        var key=(I(u["x"]),I(u["y"]));if(_guardAnchors.TryGetValue(key,out var cached))return cached;
        var r=_layout!.Regions.First(r=>r.Input.X==I(u["x"])&&r.Input.Y==I(u["y"]));
        foreach(var p in new[]{new Vector2(144,120),new Vector2(96,128),new Vector2(160,160),new Vector2(64,64),new Vector2(192,192)})
            if(WalkSafe(r,p))return _guardAnchors[key]=new Vector2(r.Input.X*256,r.Input.Y*256)+p;
        return _guardAnchors[key]=new Vector2(r.Input.X*256+128,r.Input.Y*256+128);
    }
    private Vector2 TroopPosition(JsonNode unit)
    {
        if(_beat?["motions"] is JsonArray motions)
        {
            var motion=motions.FirstOrDefault(m=>m!["unit"]!.ToString()==DesktopScene.Key("armies",unit));
            if(motion?["points"] is JsonArray points && points.Count>1)
            {
                float t=_beatProgress*(points.Count-1);int i=Math.Min((int)t,points.Count-2);
                var a=new Vector2(I(points[i]![0]),I(points[i]![1]));
                var b=new Vector2(I(points[i+1]![0]),I(points[i+1]![1]));
                return (a.Lerp(b,Math.Clamp(t-i,0,1))+Vector2.One*.5f)*256;
            }
        }
        return (new Vector2(I(unit["x"]),I(unit["y"]))+Vector2.One*.5f)*256;
    }
    private void DrawTroops()
    {
        VisibleSoldiers=0;if(Frame==null||_layout==null)return;
        if(ShowGuards&&Frame["scene"]?["guards"] is JsonArray guards)
            foreach(var u in guards)
            {
                var world=GuardAnchor(u!);var at=Screen(world);if(!OnScreen(at))continue;
                if(_scale<.3f)
                {
                    DrawRect(new Rect2(at-new Vector2(3,2),new Vector2(6,4)),new Color("896f4a"));
                    DrawLine(at+new Vector2(-3,-2),at+new Vector2(0,-5),new Color("c2aa7c"),1);continue;
                }
                float size=Math.Max(.38f,_scale);
                DrawCircle(at+new Vector2(0,9)*size,5*size,new Color("795d42"));
                for(int i=0;i<6;i++)
                {
                    var p=at+new Vector2(i%3*7-7,i/3*10-7)*size;
                    p+=new Vector2((float)Math.Sin(_time*.6+i)*3,(float)Math.Cos(_time*.5+i)*2)*_scale;
                    MiniPerson(p,new Color("977553"),size,_time+i,moving:_scale>.3f,soldier:true,wild:true);
                }
            }
        if(!Units&&!ShowGuards)return;
        string Route(JsonNode u)=>_beat?["motions"]?.AsArray().FirstOrDefault(m=>m!["unit"]!.ToString()==DesktopScene.Key("armies",u))?["points"]?.ToJsonString()??"";
        var groups=Frame["armies"]!.AsArray().Where(u=>F(u!["hp"])>0).GroupBy(u=>(I(u!["x"]),I(u["y"]),S(u["owner"]),S(u["kind"]),Route(u)));
        foreach(var group in groups)
        {
            var first=group.First()!;var at=Screen(TroopPosition(first));
            if(!OnScreen(at,80))continue;
            bool wild=group.Key.Item3=="野人";if(wild&&!ShowGuards)continue;
            if(!wild&&!Units)continue;
            var color=wild?new Color("ac8b63"):NationColor(group.Key.Item3);
            float size=Math.Clamp(_scale,.40f,1.3f);
            bool engaged=first["engaged"]?.GetValue<bool>()==true;
            var owners=Frame["armies"]!.AsArray().Where(u=>I(u!["x"])==group.Key.Item1&&I(u["y"])==group.Key.Item2).Select(u=>S(u!["owner"])).Distinct().OrderBy(s=>s,StringComparer.Ordinal).ToArray();
            int ownerIndex=Array.IndexOf(owners,group.Key.Item3);float side=ownerIndex%2==0?-1:1;
            int count=DesktopScene.SquadFigures(group.Count()),columns=count<=16?4:count<=32?6:8;
            if(_scale<.3f)count=Math.Min(count,8);
            bool moving=_beat?["phase"]?.ToString()=="march"&&_beat["motions"]!.AsArray().Any(m=>m!["unit"]!.ToString()==DesktopScene.Key("armies",first));
            double clock=moving?_beatProgress*5:_time;
            bool fighting=_beat?["phase"]?.ToString()=="battle"&&_beat["position"] is JsonArray battleAt&&I(battleAt[0])==group.Key.Item1&&I(battleAt[1])==group.Key.Item2&&
                (_beat["combat"]?["enemies"]?[group.Key.Item3] is JsonArray enemies?enemies.Count>0:_beat["combat"]?["damage"]?[group.Key.Item3]!=null);
            if(fighting)clock=_beatProgress*5;
            if(owners.Length>1)at+=new Vector2(side*(fighting?18:34),ownerIndex/2*32)*size;
            for(int i=0;i<count;i++)
            {
                var p=at+new Vector2((i%columns-(columns-1)*.5f)*8,(i/columns)*10-12)*size;
                if(fighting)p.X-=(float)Math.Sin(_beatProgress*24+i)*2*side*size;
                MiniPerson(p,color,size,clock+i*.1,moving,soldier:true,horse:group.Key.Item4.EndsWith("cavalry"),fighting:fighting,wild:wild,facingRight:owners.Length<2||side<0);VisibleSoldiers++;
            }
            DrawLine(at+new Vector2(-5,-21)*size,at+new Vector2(-5,-6)*size,new Color("d4cab1"),Math.Max(1,size));
            DrawRect(new Rect2(at+new Vector2(-5,-21)*size,new Vector2(13,7)*size),color);
            DrawString(_font,at+new Vector2(13,-12)*size,group.Count()+"支",fontSize:12,modulate:new Color("fff3c8"));
        }
    }
    private Vector2 BuildingAnchor(int id)
    {
        string wanted=$"b/{id:D10}";
        foreach(var r in _layout!.Regions)
            foreach(var b in r.Structures)
                if(b.Id==wanted)return new Vector2(r.Input.X*256+b.Bounds.X+b.Bounds.Width*.5f,r.Input.Y*256+b.Bounds.Y);
        return Vector2.Zero;
    }
    private string ResourceName(string id)=>id switch {"native:gold"=>"金","native:food"=>"粮","native:wood"=>"木","native:ore"=>"矿","native:oil"=>"油","native:equipment"=>"装备","native:supply"=>"补给","hp"=>"HP",_=>id};
    private void ResourceIcon(Vector2 p,string resource,float alpha)
    {
        Color C(string hex)=>new Color(new Color(hex),alpha);
        switch(resource)
        {
            case "native:gold":DrawCircle(p,5,C("d9bd66"));DrawCircle(p,3,C("f1d999"));break;
            case "native:wood":for(int i=0;i<3;i++)DrawLine(p+new Vector2(-5,i*3-3),p+new Vector2(5,i*3-3),C("bc9464"),2);break;
            case "native:equipment":DrawLine(p+new Vector2(-4,-5),p+new Vector2(4,5),C("d4dfdb"),2);DrawLine(p+new Vector2(4,-5),p+new Vector2(-4,5),C("bcc9c7"),2);break;
            case "native:supply":DrawRect(new Rect2(p-new Vector2(5,5),new Vector2(10,10)),C("b89461"));DrawLine(p-new Vector2(4,4),p+new Vector2(4,4),C("e5cca0"),1);break;
            case "native:food":DrawLine(p+new Vector2(0,5),p-new Vector2(0,5),C("d8c270"),1);for(int i=0;i<3;i++)DrawLine(p+new Vector2(-4,i*3-5),p+new Vector2(4,i*3-2),C("ecd891"),2);break;
            case "hp":DrawCircle(p+new Vector2(-2,-2),3,C("e49181"));DrawCircle(p+new Vector2(2,-2),3,C("e49181"));DrawColoredPolygon(new[]{p+new Vector2(-5,-1),p+new Vector2(5,-1),p+new Vector2(0,6)},C("e49181"));break;
            default:DrawCircle(p,5,C(resource=="native:oil"?"716f77":"aab6ac"));break;
        }
    }
    private void DrawBeatEffects()
    {
        if(_beat==null)return;
        if(_beat["pops"] is JsonArray pops)
            for(int i=0;i<pops.Count;i++)
            {
                var pop=pops[i]!;Vector2 anchor;
                if(pop["building"]!=null)anchor=BuildingAnchor(I(pop["building"]));
                else {var p=pop["position"]!;anchor=(new Vector2(I(p[0]),I(p[1]))+Vector2.One*.5f)*256;}
                var at=Screen(anchor)+new Vector2((i%3-1)*45,-18-i/3*22-_beatProgress*35);
                float alpha=Math.Min(1,(1-_beatProgress)*4);double amount=F(pop["amount"]);
                string resource=S(pop["resource"]),number=pop["amount"]==null?"":$"{(amount>0?"+":"")}{amount:G4}";
                var color=new Color(new Color(amount<0?"efbc80":"c9efa3"),alpha);
                ResourceIcon(at-new Vector2(10,5),resource,alpha);
                DrawString(_font,at,ResourceName(resource)+number,fontSize:15,modulate:color);
            }
        string phase=S(_beat["phase"]);
        if(phase is "battle" or "casualties" && _beat["position"] is JsonArray cell)
        {
            var at=CellCenter(new Vector2I(I(cell[0]),I(cell[1])));
            if(phase=="battle")
                for(int i=0;i<4;i++)
                {
                    float t=(_beatProgress*5+i*.23f)%1;
                    var p=at+new Vector2((float)Math.Sin(i*3)*17,(float)Math.Cos(i*3)*12);
                    DrawLine(p+new Vector2(-7,-8)*t,p+new Vector2(7,8)*t,new Color(1,.9f,.64f,1-t),2);
                }
            else if(_beat["combat"] is JsonObject combat)
            {
                var after=combat["after"]!.AsArray().GroupBy(u=>S(u!["owner"])).ToDictionary(g=>g.Key,g=>g.Sum(u=>F(u!["hp"])));
                int line=0;foreach(var group in combat["before"]!.AsArray().GroupBy(u=>S(u!["owner"])))
                {
                    double loss=group.Sum(u=>F(u!["hp"]))-after.GetValueOrDefault(group.Key);
                    if(loss<=0)continue;
                    DrawString(_font,at+new Vector2(-45,line++*20-30-_beatProgress*15),$"{(Frame!["names"]?[group.Key]?.ToString()??group.Key)} -{loss:G4}HP",fontSize:16,modulate:new Color("f2b49a"));
                }
                var surviving=combat["after"]!.AsArray().Select(u=>DesktopScene.Key("armies",u!)).ToHashSet();
                var fallen=combat["before"]!.AsArray().Where(u=>!surviving.Contains(DesktopScene.Key("armies",u!))).ToArray();
                for(int i=0;i<Math.Min(32,fallen.Length*8);i++)
                {
                    var p=at+new Vector2(i%8*7-28,i/8*6+12);
                    DrawRect(new Rect2(p,new Vector2(6,3)),new Color(new Color("8b7968"),1-_beatProgress));
                    DrawCircle(p+new Vector2(6,1),1.5f,new Color(new Color("c4b095"),1-_beatProgress));
                }
            }
        }
        if(phase=="complete"&&int.TryParse(S(_beat["source"]).Split('/').Last(),out int id))
        {
            var at=Screen(BuildingAnchor(id));DrawArc(at,8+_beatProgress*24,0,Mathf.Tau,24,new Color(1,.9f,.55f,1-_beatProgress),2);
        }
    }
}
