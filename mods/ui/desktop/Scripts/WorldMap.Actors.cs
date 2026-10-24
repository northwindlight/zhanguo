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
    // Smooth camera: world anchor + target scale lerped in _Process; render
    // jitter is added by battle shake and must reach every draw path.
    private Vector2? _cameraAnchor;
    private float _cameraScale=1;
    private Vector2 _jitter;
    private float _shakeAmp;
    private int _battleStep=-1;
    private HashSet<string>? _knownSites;
    private readonly Dictionary<RegionPlan,(Vector2 A, Vector2 B)[]> _walks=new();
    private readonly Dictionary<(int,int),Vector2> _guardAnchors=new();
    public int Residents=>_layout?.Regions.Sum(r=>r.People.Count)??0;
    public int Guards=>Frame?["scene"]?["guards"]?.AsArray().Count??0;
    public int RegionNodes=>_artwork.RegionNodeCount;
    public float PoliticalOpacity=>DesktopScene.NationOpacity(_scale);
    public int VisibleResidents { get; private set; }
    public int VisibleSoldiers { get; private set; }
    public bool ShowGuards=true;

    // Ambient particles live in world coordinates and advance on the 24Hz
    // overlay tick; they are pure presentation and never touch rule state.
    private const byte KindDust=0,KindSmoke=1,KindSpark=2,KindArrow=3,KindSparkle=4,KindRipple=5,KindDebris=6;
    private struct Particle{public Vector2 Pos;public Vector2 Vel;public float Life,Max,Size;public Color Tint;public byte Kind;}
    private readonly List<Particle> _particles=new();
    private int _spawnSalt;

    public void BeginBeat(JsonObject beat,bool follow)
    {
        _beat=beat;_beatProgress=0;_battleStep=-1;
        DesktopScene.Apply(Frame!,beat["delta"]!.AsObject());SetFrame(Frame!);
        string phase=S(beat["phase"]);
        if(phase=="battle")_shakeAmp=1;
        else if(phase=="casualties")_shakeAmp=.45f;
        if(follow&&beat["position"] is JsonArray p)
        {
            _fit=false;
            _cameraScale=phase switch{
                "battle" or "casualties"=>1.55f,
                "build" or "complete" or "recruit" or "production" or "maintenance" or "occupation"=>1.45f,
                "march"=>1.0f,
                "engage" or "arrival"=>1.25f,
                _=>1.15f};
            _cameraAnchor=FocusAnchor(phase,beat,I(p[0]),I(p[1]));
            UpdateView();
        }
        BeatBurst();
    }
    // Building actions frame the site itself, not just the tile center.
    private Vector2 FocusAnchor(string phase,JsonObject beat,int x,int y)
    {
        if(phase=="complete"&&int.TryParse(S(beat["source"]).Split('/').Last(),out var id))
        {
            var rect=BuildingRect(id);
            if(rect.Size!=Vector2.Zero)return rect.Position+rect.Size*.5f+new Vector2(0,6);
        }
        if(phase=="build")
        {
            var site=_layout?.Regions.FirstOrDefault(r=>r.Input.X==x&&r.Input.Y==y)?.Structures.FirstOrDefault(s=>s.Remaining>0);
            if(site!=null)return new Vector2(x*256+site.Bounds.X+site.Bounds.Width*.5f,y*256+site.Bounds.Y+site.Bounds.Height*.5f);
        }
        return (new Vector2(x,y)+Vector2.One*.5f)*256;
    }
    public void BeatProgress(float progress) { _beatProgress=progress;QueueRedraw(); }
    public void EndBeat() { _beat=null;_beatProgress=0;QueueRedraw(); }
    private Vector2 Screen(Vector2 world)=>_offset+_jitter+world*_scale;
    private bool OnScreen(Vector2 p,float margin=40)=>p.X>=-margin&&p.Y>=-margin&&p.X<=Size.X+margin&&p.Y<=Size.Y+margin;
    private bool RegionOnScreen(RegionPlan r)
    {
        var origin=new Vector2(r.Input.X*256,r.Input.Y*256);
        return new Rect2(_offset+origin*_scale,Vector2.One*256*_scale).Grow(30).Intersects(new Rect2(Vector2.Zero,Size));
    }

    private bool WalkSafe(RegionPlan r,Vector2 p)
    {
        var body=new Footprint(p.X-3,p.Y-8,6,8);
        return _layout!.CanBuild(r.Input,body)&&!r.Structures.Any(b=>b.Bounds.Overlaps(body,3))&&
            !r.Trees.Any(t=>new Footprint(t.Foot.X-7,t.Foot.Y-15,14,15).Overlaps(body,1))&&
            !r.Rocks.Any(t=>new Footprint(t.X-7,t.Y-12,14,12).Overlaps(body,1));
    }
    private (Vector2 A, Vector2 B)[] Walks(RegionPlan r)
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

    // Detailed code-drawn figure, ~13 units tall (16 with horse) at size 1.
    // Coordinates face right; X() mirrors every part for left-facing figures.
    // If a matching sheet exists under assets/actors it wins over this drawing.
    private static readonly string[] Skins={"e8c39a","d9a878","c68e5f","b57c4e"};
    private static readonly string[] Hairs={"3a2e24","54432f","777770","2c2c31"};
    private void MiniPerson(Vector2 foot,Color cloth,float size,double clock,bool moving=false,bool soldier=false,bool horse=false,bool working=false,float alpha=1,bool fighting=false,bool wild=false,bool facingRight=true,int seed=0)
    {
        float s=size;
        float phase=(float)(clock*7);
        float swing=moving?MathF.Sin(phase):0;
        float bob=moving?MathF.Abs(MathF.Cos(phase))*.7f:0;
        float X(float x)=>facingRight?x:-x;
        Color C(string v)=>new Color(new Color(v),alpha);
        void Box(float x,float y,float w,float h,Color c)=>DrawRect(new Rect2(foot+new Vector2(X(x),y-bob)*s,new Vector2(w,h)*s),c);
        void Line(float x1,float y1,float x2,float y2,Color c,float width=1)
            =>DrawLine(foot+new Vector2(X(x1),y1-bob)*s,foot+new Vector2(X(x2),y2-bob)*s,c,Math.Max(.8f,width*s));
        var sheet = horse?ActorSheets.Load("soldier-cavalry"):soldier?ActorSheets.Load("soldier-infantry")
            :working?ActorSheets.Load("worker"):ActorSheets.Load("resident-walk");
        if(sheet!=null)
        {
            DrawCircle(foot+new Vector2(0,1)*s,(horse?4.6f:3.4f)*s,new Color(0,0,0,.16f*alpha));
            int frame=(moving||fighting)?(int)(clock*sheet.Frames)%sheet.Frames:0;
            var row=ActorSheets.Row(facingRight,fighting,sheet.Rows);
            var tint=new Color(1,1,1,alpha).Lerp(new Color(cloth,alpha),.42f);
            var at=foot+new Vector2(-sheet.CellW*.5f,-sheet.CellH+1)*s;
            DrawTextureRectRegion(sheet.Texture,new Rect2(at,new Vector2(sheet.CellW,sheet.CellH)*s),
                new Rect2(frame*sheet.CellW,row*sheet.CellH,sheet.CellW,sheet.CellH),tint);
            return;
        }
        string skin=Skins[seed&3],hair=Hairs[(seed>>2)&3];
        var tunic=cloth;
        if((seed>>4)%3==1)tunic=cloth.Lightened(.07f);
        else if((seed>>4)%3==2)tunic=cloth.Darkened(.07f);
        var outline=new Color(.12f,.10f,.08f,.30f*alpha);
        DrawCircle(foot+new Vector2(0,1)*s,(horse?4.6f:3.4f)*s,new Color(0,0,0,.16f*alpha));
        if(horse)
        {
            int gallop=(int)(clock*10)%2;
            Box(-2.9f,-11.2f,5.7f,5.7f,outline);Box(-1.2f,-14.5f,3.5f,3.5f,outline);
            Line(-4.2f,-7.0f,-5.8f,-5.2f+MathF.Sin((float)clock*6)*.8f,C("5b4632"),1.2f);
            Box(-3.6f,-4.2f,1.2f,4.2f-(gallop==1?1.0f:0),C("403d36"));
            Box(-2.2f,-4.2f,1.2f,4.2f-(gallop==1?1.0f:0),C("403d36"));
            Box(2.4f,-4.2f,1.2f,4.2f-(gallop==0?1.0f:0),C("403d36"));
            Box(3.9f,-4.2f,1.2f,4.2f-(gallop==0?1.0f:0),C("403d36"));
            Box(-4.2f,-7.8f,8.8f,3.6f,C("8a6748"));
            Box(-4.2f,-5.2f,8.8f,1.0f,C("6f5238"));
            Box(3.6f,-10.0f,2.4f,4.2f,C("8a6748"));
            Box(4.6f,-10.9f,3.2f,1.7f,C("9a7856"));
            Box(7.4f,-10.6f,.9f,.9f,C("5b4632"));
            Box(3.6f,-10.4f,1.0f,3.6f,C("4a3a2a"));
            Box(-1.3f,-11.8f,3.4f,4.4f,new Color(tunic,alpha));
            Box(-1.3f,-11.8f,3.4f,.8f,C("574a33"));
            Box(-1.0f,-14.0f,3.0f,2.7f,C(skin));
            Box(-1.2f,-14.4f,3.4f,1.1f,C(wild?"7e6249":"b4c2bb"));
            if(!wild)Box(-.2f,-15.1f,1.0f,.8f,new Color(cloth,alpha));
            float reach=fighting?MathF.Sin((float)clock*10)*2:0;
            Line(.5f,-10.6f,8.5f+reach,-9.8f,C("c9b98a"),1);
            Line(8.5f+reach,-10.1f,9.4f+reach,-9.6f,C("dfe3da"),1);
            return;
        }
        Box(-2.9f,-10.9f,5.8f,5.8f,outline);
        Box(-2.0f,-13.9f,4.0f,3.6f,outline);
        float stride=moving?swing*1.3f:0;
        Box(-1.9f+stride,-5.2f,1.5f,5.2f,C("4a4438"));
        Box(.4f-stride,-5.2f,1.5f,5.2f,C("4a4438"));
        Box(-1.9f+stride,-1.1f,1.5f,1.1f,C("332e26"));
        Box(.4f-stride,-1.1f,1.5f,1.1f,C("332e26"));
        float arm=moving?-swing*1.1f:0;
        Box(-1.5f-arm*.7f,-9.8f,1.2f,4.2f,new Color(tunic.Darkened(.3f),alpha));
        Box(-2.3f,-10.2f,4.6f,5.2f,new Color(tunic,alpha));
        Box(1.1f,-10.2f,1.2f,5.2f,new Color(tunic.Darkened(.22f),alpha));
        Box(-2.3f,-5.8f,4.6f,.9f,C("574a33"));
        if(working)Box(-1.8f,-8.6f,3.6f,2.9f,C("7a6a4d"));
        Box(.3f+arm,-9.8f,1.2f,4.2f,new Color(tunic,alpha));
        Box(.3f+arm,-5.9f,1.3f,1.3f,C(skin));
        Box(-1.7f,-13.6f,3.4f,3.2f,C(skin));
        Box(-1.9f,-14.0f,3.8f,1.2f,C(hair));
        Box(-1.95f,-13.6f,1.0f,1.7f,C(hair));
        Box(.6f,-12.4f,.7f,.7f,C("2c2620"));
        if(soldier)
        {
            Box(-2.0f,-14.6f,4.0f,1.4f,C(wild?"7e6249":"b4c2bb"));
            if(wild)Box(-1.6f,-15.0f,1.1f,.7f,C("6b5a3e"));
            else Box(-.5f,-15.3f,1.2f,.9f,new Color(cloth,alpha));
            if(fighting)
            {
                float reach=MathF.Sin((float)clock*14)*2.5f;
                Line(.8f,-8.2f,7.5f+reach,-9.6f,C(wild?"a68c67":"c9b98a"),1);
                Line(7.5f+reach,-9.9f,8.6f+reach,-9.4f,C("dfe3da"),1);
            }
            else
            {
                Line(-1.6f,-4.5f,-2.6f+swing*.4f,-15.6f,C(wild?"a68c67":"c9b98a"),1);
                Line(-2.75f+swing*.4f,-15.9f,-2.45f+swing*.4f,-14.6f,C("dfe3da"),1);
            }
            Box(1.9f,-9.4f,1.7f,3.4f,new Color(cloth.Darkened(.15f),alpha));
            Box(2.45f,-8.4f,.8f,.8f,C("e7e5cd"));
        }
        else
        {
            if((seed>>6)%4==0)Box(-2.1f,-14.2f,4.2f,1.0f,C("9c8056"));
            if(moving&&!working)Box(1.2f,-8.9f,2.0f,2.5f,C("bba77d"));
            if(working)
            {
                float tool=MathF.Sin((float)clock*11)*3;
                Line(.8f,-8.5f,4.6f,-11.5f+tool,C("d5c6a0"),1);
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
            if(!RegionOnScreen(r))continue;
            var walks=Walks(r);
            for(int i=0;i<walks.Length;i++)
            {
                if(_scale<.3f&&i%5!=0)continue;
                float t=(float)((_time/9+i*.193+r.Input.X*.13)%1);
                float travel=t<.4f?t/.4f:t<.55f?1:t<.95f?1-(t-.55f)/.4f:0;
                var p=Screen(origin+walks[i].A.Lerp(walks[i].B,travel));if(!OnScreen(p))continue;
                bool moving=walks[i].A!=walks[i].B&&(t<.4f||t is >.55f and <.95f);
                MiniPerson(p,new Color(i%3==0?"b59a67":i%3==1?"728c8a":"9b7765"),Math.Max(.32f,_scale),_time+i,moving,seed:i);VisibleResidents++;
            }
            foreach(var b in r.Structures.Where(b=>b.Remaining>0))
                for(int i=0;i<3;i++)
                {
                    var at=origin+new Vector2(b.Bounds.X-6+i*10,b.Bounds.Y+b.Bounds.Height+6);
                    if(_layout.Water(at.X,at.Y)>.15f)continue;
                    var p=Screen(at);if(!OnScreen(p))continue;
                    MiniPerson(p,new Color("c9ab72"),_scale,_time+i,working:true,seed:i+7);VisibleResidents++;
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
    private Vector2 MotionWorld(JsonNode motion)
    {
        var points=motion["points"]!.AsArray();
        float t=_beatProgress*(points.Count-1);int i=Math.Min((int)t,points.Count-2);
        var a=new Vector2(I(points[i]![0]),I(points[i]![1]));
        var b=new Vector2(I(points[i+1]![0]),I(points[i+1]![1]));
        return (a.Lerp(b,Math.Clamp(t-i,0,1))+Vector2.One*.5f)*256;
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
                    MiniPerson(p,new Color("977553"),size,_time+i,moving:_scale>.3f,soldier:true,wild:true,seed:i+3);
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
            if(owners.Length>1)
            {
                // Engaged sides close in over the beat and keep clashing.
                float close= fighting? Math.Clamp(_beatProgress*1.7f,0,1):0;
                float gap=34+(12-34)*close+(fighting?MathF.Sin(_beatProgress*22)*3:0);
                at+=new Vector2(side*gap,ownerIndex/2*32)*size;
            }
            // Recruit beats muster: figures walk from the settlement to rank.
            var rpos=_beat?["position"] as JsonArray;
            bool mustering=!fighting&&!moving&&S(_beat?["phase"])=="recruit"&&rpos!=null&&I(rpos[0])==group.Key.Item1&&I(rpos[1])==group.Key.Item2;
            var musterOrigin=Vector2.Zero;float musterT=0;
            if(mustering)
            {
                musterOrigin=Screen((new Vector2(I(rpos![0]),I(rpos[1]))+Vector2.One*.5f)*256);
                musterT=Math.Clamp(_beatProgress*1.5f,0,1);musterT=musterT*musterT*(3-2*musterT);
            }
            for(int i=0;i<count;i++)
            {
                var spot=at+new Vector2((i%columns-(columns-1)*.5f)*8,(i/columns)*10-12)*size;
                if(fighting)spot.X-=(float)Math.Sin(_beatProgress*24+i)*2*side*size;
                var p=mustering?musterOrigin.Lerp(spot,musterT):spot;
                bool stepping=moving||mustering;
                bool face=mustering?spot.X>=musterOrigin.X:(owners.Length<2||side<0);
                MiniPerson(p,color,size,clock+i*.1,stepping,soldier:true,horse:group.Key.Item4.EndsWith("cavalry"),fighting:fighting,wild:wild,facingRight:face,seed:i);VisibleSoldiers++;
            }
            var pole=at+new Vector2(-5,-21)*size;
            DrawLine(pole,pole+new Vector2(0,15)*size,new Color("d4cab1"),Math.Max(1,size));
            DrawCircle(pole,Math.Max(.8f,size*.8f),new Color("e8dcb0"));
            float w1=MathF.Sin((float)_time*7+at.X*.13f)*1.1f*size,w2=MathF.Sin((float)_time*7+at.X*.13f+1.4f)*1.6f*size;
            DrawColoredPolygon(new[]{pole,pole+new Vector2(13*size,w1),pole+new Vector2(13*size,7*size+w2),pole+new Vector2(0,7*size)},color);
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
    private Rect2 BuildingRect(int id)
    {
        string wanted=$"b/{id:D10}";
        foreach(var r in _layout!.Regions)
            foreach(var b in r.Structures)
                if(b.Id==wanted)return new Rect2(new Vector2(r.Input.X*256+b.Bounds.X,r.Input.Y*256+b.Bounds.Y),new Vector2(b.Bounds.Width,b.Bounds.Height));
        return new Rect2();
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
                    float fade=1-_beatProgress;
                    DrawCircle(p+new Vector2(2,1),2.2f,new Color(new Color("6e3f35"),fade*.5f));
                    DrawRect(new Rect2(p+new Vector2(0,-2),new Vector2(6,2)),new Color(new Color("8b7968"),fade));
                    DrawCircle(p+new Vector2(6,-2),1.4f,new Color(new Color("c4b095"),fade));
                }
            }
        }
        if(phase=="complete"&&int.TryParse(S(_beat["source"]).Split('/').Last(),out int id))
        {
            var at=Screen(BuildingAnchor(id));DrawArc(at,8+_beatProgress*24,0,Mathf.Tau,24,new Color(1,.9f,.55f,1-_beatProgress),2);
            // Unveiling: gold frame pulses outward while settling dust clears
            // off the footprint that just switched from scaffold to finished.
            var rect=BuildingRect(id);
            if(rect.Size!=Vector2.Zero)
            {
                var origin=Screen(rect.Position);var size=rect.Size*_scale;
                float grow=_beatProgress*8*Math.Max(.7f,_scale);
                DrawRect(new Rect2(origin-new Vector2(grow,grow),size+new Vector2(grow,grow)*2),new Color(1,.92f,.6f,(1-_beatProgress)*.9f),false,2);
                float cover=Math.Max(0,.35f-_beatProgress)/.35f;
                if(cover>0)
                {
                    float radius=MathF.Max(3,size.Length()*.22f);
                    var dust=new Color(new Color("cbbf9d"),cover*.45f);
                    DrawCircle(origin,radius*1.3f,dust);
                    DrawCircle(origin+new Vector2(size.X,0),radius*1.3f,dust);
                    DrawCircle(origin+new Vector2(0,size.Y),radius*1.3f,dust);
                    DrawCircle(origin+size,radius*1.3f,dust);
                    DrawCircle(origin+size*.5f,radius*1.6f,dust);
                }
            }
        }
    }

    private void Spawn(Particle p){ if(_particles.Count<260)_particles.Add(p); }
    private void BeatBurst()
    {
        if(_beat==null)return;
        string phase=S(_beat["phase"]);
        Vector2 at;
        if(_beat["position"] is JsonArray cell)at=(new Vector2(I(cell[0]),I(cell[1]))+Vector2.One*.5f)*256;
        else if(phase=="complete"&&int.TryParse(S(_beat["source"]).Split('/').Last(),out var id))at=BuildingAnchor(id);
        else return;
        switch(phase)
        {
            case "occupation":
                Spawn(new Particle{Pos=at,Vel=Vector2.Zero,Life=1.4f,Max=1.4f,Size=4,Tint=new Color("ffe9a0"),Kind=KindRipple});
                for(int i=0;i<10;i++)Spawn(new Particle{Pos=at+new Vector2(i*7-31,(i*11%13)-6),Vel=new Vector2(0,-16),Life=.9f,Max=.9f,Size=1.4f,Tint=new Color("c9efa3"),Kind=KindSparkle});
                break;
            case "recruit":
                Spawn(new Particle{Pos=at,Vel=Vector2.Zero,Life=1.2f,Max=1.2f,Size=3,Tint=new Color("ffe9a0"),Kind=KindRipple});
                for(int i=0;i<6;i++)Spawn(new Particle{Pos=at+new Vector2(i*6-15,2+(i*7%5)),Vel=new Vector2((i%3-1)*4,-6),Life=.8f,Max=.8f,Size=1.5f+_scale*.5f,Tint=new Color("b8a884"),Kind=KindDust});
                break;
            case "battle":
                Spawn(new Particle{Pos=at,Vel=Vector2.Zero,Life=1.0f,Max=1.0f,Size=5,Tint=new Color("f2e2b0"),Kind=KindRipple});
                for(int i=0;i<6;i++)Spawn(new Particle{Pos=at+new Vector2(i*9-22,3+(i*5%6)),Vel=new Vector2((i%3-1)*7,-9),Life=.6f,Max=.6f,Size=1.6f+_scale*.5f,Tint=new Color("b0a488"),Kind=KindDust});
                break;
            case "healing":
                for(int i=0;i<6;i++)Spawn(new Particle{Pos=at+new Vector2(i*9-22,(i*7%19)-8),Vel=new Vector2(0,-5),Life=.8f,Max=.8f,Size=1.5f,Tint=new Color("b8efad"),Kind=KindSparkle});
                break;
            case "complete":
                Spawn(new Particle{Pos=at,Vel=Vector2.Zero,Life=1.1f,Max=1.1f,Size=3,Tint=new Color("ffe9a0"),Kind=KindRipple});
                for(int i=0;i<8;i++)Spawn(new Particle{Pos=at+new Vector2(i*8-28,(i*13%11)-4),Vel=new Vector2(0,-13),Life=.9f,Max=.9f,Size=1.4f,Tint=new Color("ffe9a0"),Kind=KindSparkle});
                for(int i=0;i<6;i++)Spawn(new Particle{Pos=at+new Vector2(i*7-17,4+(i*5%7)),Vel=new Vector2((i%3-1)*8,-10),Life=.7f,Max=.7f,Size=1.5f+_scale*.5f,Tint=new Color("cbbf9d"),Kind=KindDust});
                break;
            case "casualties":
                for(int i=0;i<8;i++)Spawn(new Particle{Pos=at+new Vector2(i*9-31,4+(i*5%7)),Vel=new Vector2((i%3-1)*6,-22),Life=.5f,Max=.5f,Size=1.1f,Tint=new Color("7a6a58"),Kind=KindDebris});
                break;
            case "build":
                for(int i=0;i<5;i++)Spawn(new Particle{Pos=at+new Vector2(i*6-12,2),Vel=new Vector2(i%3-1,-5),Life=.8f,Max=.8f,Size=1.6f,Tint=new Color("b8a884"),Kind=KindDust});
                break;
        }
    }
    private void AmbientTick()
    {
        float dt=1f/24f;
        for(int i=_particles.Count-1;i>=0;i--)
        {
            var p=_particles[i];
            p.Life-=dt;
            if(p.Life<=0){_particles.RemoveAt(i);continue;}
            p.Pos+=p.Vel*dt;
            if(p.Kind==KindArrow)p.Vel+=new Vector2(0,150f)*dt;
            else if(p.Kind is KindSpark or KindDebris)p.Vel+=new Vector2(0,60f)*dt;
            else if(p.Kind==KindSmoke)p.Vel=p.Vel.Lerp(new Vector2(6,-7),.06f);
            _particles[i]=p;
        }
        if(Frame==null||_layout==null)return;
        SpawnStructureLife();
        SpawnBeatLife();
    }
    private void SpawnStructureLife()
    {
        if(_layout==null||_scale<.32f||_particles.Count>190)return;
        int budget=8;int tick=(int)_time;
        foreach(var r in _layout.Regions)
        {
            if(!RegionOnScreen(r))continue;
            var origin=new Vector2(r.Input.X*256,r.Input.Y*256);
            foreach(var b in r.Structures)
            {
                int hash=(int)(_layout.Hash(r.Input.X*256+(int)b.Bounds.X,r.Input.Y*256+(int)b.Bounds.Y,130) & int.MaxValue);
                var anchor=origin+new Vector2(b.Bounds.X+b.Bounds.Width-3,b.Bounds.Y+2);
                if(b.Remaining>0)
                {
                    if((tick+hash)%2!=0)continue;
                    Spawn(new Particle{Pos=anchor+new Vector2(hash%5-2,2),Vel=new Vector2(2,-3),Life=.7f,Max=.7f,Size=1.6f+_scale,Tint=new Color("b8a884"),Kind=KindDust});
                }
                else
                {
                    int period=2+hash%3;
                    if((tick/period+hash)%3!=0)continue;
                    Spawn(new Particle{Pos=anchor,Vel=new Vector2(3,-6),Life=2.6f,Max=2.6f,Size=1.4f+_scale*.8f,Tint=new Color("cfd4cd"),Kind=KindSmoke});
                }
                if(--budget<=0)return;
            }
        }
    }
    private void SpawnBeatLife()
    {
        if(_beat==null||_particles.Count>230)return;
        string phase=S(_beat["phase"]);
        if(_beat["position"] is JsonArray cell)
        {
            var center=(new Vector2(I(cell[0]),I(cell[1]))+Vector2.One*.5f)*256;
            if(phase=="battle"&&_beatProgress<.9f)
            {
                int step=(int)(_beatProgress*4);
                if(step!=_battleStep)
                {
                    _battleStep=step;
                    Spawn(new Particle{Pos=center,Vel=Vector2.Zero,Life=.6f,Max=.6f,Size=2,Tint=new Color("f2e2b0"),Kind=KindRipple});
                    for(int i=0;i<3;i++)Spawn(new Particle{Pos=center+new Vector2(i*9-9,(i*7%5)-2),Vel=new Vector2((i%3-1)*6,-8),Life=.5f,Max=.5f,Size=1.4f+_scale*.4f,Tint=new Color("b0a488"),Kind=KindDust});
                }
                _spawnSalt++;
                if(_spawnSalt%2==0)
                {
                    float side=_spawnSalt%4<2?1:-1;
                    float arc=3+_spawnSalt*37%7;
                    Spawn(new Particle{Pos=center+new Vector2(-side*80,-4-arc),Vel=new Vector2(side*110,-55),Life=1.0f,Max=1.0f,Size=1,Tint=default,Kind=KindArrow});
                }
                if(_spawnSalt%3==0)
                {
                    var at=center+new Vector2(_spawnSalt*29%40-20,_spawnSalt*53%26-13);
                    Spawn(new Particle{Pos=at,Vel=new Vector2(_spawnSalt%5-2,-14),Life=.35f,Max=.35f,Size=1,Tint=new Color("ffe9a0"),Kind=KindSpark});
                }
            }
            if(phase=="recruit"&&_beatProgress<.7f)
            {
                _spawnSalt++;
                if(_spawnSalt%2==0)
                {
                    float ease=Math.Clamp(_beatProgress*1.5f,0,1);ease=ease*ease*(3-2*ease);
                    var at=center.Lerp(center+new Vector2(_spawnSalt*11%24-12,-6-_spawnSalt*7%8),ease)+new Vector2(_spawnSalt*13%8-4,_spawnSalt*7%4);
                    Spawn(new Particle{Pos=at,Vel=new Vector2(1,-2),Life=.6f,Max=.6f,Size=1.2f+_scale*.5f,Tint=new Color("b0a488"),Kind=KindDust});
                }
            }
            if(phase=="healing"&&_beatProgress<.7f&&_spawnSalt++%2==0)
                Spawn(new Particle{Pos=center+new Vector2(_spawnSalt*31%36-18,_spawnSalt*17%24-12),Vel=new Vector2(0,-4),Life=.8f,Max=.8f,Size=1.6f+_scale*.5f,Tint=new Color("b8efad"),Kind=KindSparkle});
        }
        if(phase=="march"&&_beat["motions"] is JsonArray motions)
        {
            int index=0;_spawnSalt++;
            foreach(var m in motions)
            {
                var points=m!["points"]?.AsArray();
                if(points==null||points.Count<2)continue;
                if((_spawnSalt+index)%2!=0){index++;continue;}
                var at=MotionWorld(m)+new Vector2(_spawnSalt*13%8-4,_spawnSalt*7%4);
                Spawn(new Particle{Pos=at,Vel=new Vector2(1,-2),Life=.6f,Max=.6f,Size=1.2f+_scale*.6f,Tint=new Color("b0a488"),Kind=KindDust});
                index++;
            }
        }
    }
    private void DrawWindowLights()
    {
        if(_scale<.45f||_layout==null)return;
        int drawn=0;
        foreach(var r in _layout.Regions)
        {
            if(!RegionOnScreen(r))continue;
            var origin=new Vector2(r.Input.X*256,r.Input.Y*256);
            foreach(var b in r.Structures)
            {
                if(b.Remaining>0)continue;
                var at=Screen(origin+new Vector2(b.Bounds.X+b.Bounds.Width*.5f,b.Bounds.Y+6));
                if(!OnScreen(at))continue;
                int hash=(int)_layout.Hash(r.Input.X*256+(int)b.Bounds.X,r.Input.Y*256+(int)b.Bounds.Y,131);
                float tw=.30f+.30f*MathF.Sin((float)_time*2.3f+hash);
                DrawRect(new Rect2(at-new Vector2(1.1f,1.1f)*_scale,new Vector2(2.2f,2.2f)*_scale),new Color(1,.80f,.42f,tw));
                if(++drawn>=160)return;
            }
        }
    }
    private void DrawBirds()
    {
        if(_scale>.9f)return;
        float span=_size*256;
        for(int flock=0;flock<2;flock++)
        {
            float t=(float)_time*(.010f+flock*.004f)+flock*.53f;
            var head=new Vector2((t*span*1.3f+flock*900)%(span+600)-300,(t*span*.7f+flock*2000)%(span+400)-200);
            for(int i=0;i<6;i++)
            {
                var at=Screen(head+new Vector2(-i*10-(i/3)*6,(i%3)*4));
                if(!OnScreen(at))continue;
                float flap=MathF.Sin((float)_time*9+i*1.7f+flock)*2.2f;
                var c=new Color("3d4a48");
                DrawLine(at+new Vector2(-3,flap*.6f),at,c,1);
                DrawLine(at,at+new Vector2(3,flap*.6f),c,1);
            }
        }
    }
    private void DrawParticles()
    {
        foreach(var p in _particles)
        {
            var at=Screen(p.Pos);
            if(!OnScreen(at))continue;
            float t=1-p.Life/p.Max;
            switch(p.Kind)
            {
                case KindDust:
                    DrawCircle(at,p.Size*(0.6f+t*1.4f)*Math.Max(.6f,_scale),new Color(p.Tint,(1-t)*.34f));
                    break;
                case KindSmoke:
                {
                    float r=p.Size*(1+t*3.2f)*Math.Max(.6f,_scale);
                    DrawCircle(at,r,new Color(p.Tint,(1-t)*.30f));
                    DrawCircle(at+new Vector2(r*.3f,-r*.25f),r*.62f,new Color(p.Tint,(1-t)*.22f));
                    break;
                }
                case KindSpark:
                {
                    var dir=p.Vel.LengthSquared()>0?p.Vel.Normalized():Vector2.Right;
                    DrawLine(at-dir*3*Math.Max(.6f,_scale),at+dir*3*Math.Max(.6f,_scale),new Color(1,.93f,.7f,1-t),Math.Max(1,_scale));
                    break;
                }
                case KindArrow:
                {
                    var dir=p.Vel.Normalized();
                    DrawLine(at-dir*4.5f*Math.Max(.6f,_scale),at+dir*4.5f*Math.Max(.6f,_scale),new Color("e8dcc0"),Math.Max(1,_scale));
                    DrawLine(at+dir*4.5f*Math.Max(.6f,_scale),at+dir*6*Math.Max(.6f,_scale),new Color("c9c2ae"),Math.Max(1,_scale));
                    break;
                }
                case KindSparkle:
                {
                    float tw=.5f+.5f*MathF.Sin((float)_time*13+p.Pos.X);
                    float r=p.Size*Math.Max(.6f,_scale);
                    var c=new Color(p.Tint,(1-t)*(.5f+.5f*tw));
                    DrawLine(at+new Vector2(-r,0),at+new Vector2(r,0),c,1);
                    DrawLine(at+new Vector2(0,-r),at+new Vector2(0,r),c,1);
                    break;
                }
                case KindRipple:
                    DrawArc(at,p.Size+t*30*Math.Max(.6f,_scale),0,Mathf.Tau,24,new Color(p.Tint,(1-t)*.6f),Math.Max(1,_scale));
                    break;
                case KindDebris:
                    DrawRect(new Rect2(at-new Vector2(.5f,.5f),Vector2.One*Math.Max(1,p.Size*_scale)),new Color(p.Tint,1-t));
                    break;
            }
        }
    }
    private void DrawAmbient()
    {
        DrawWindowLights();
        DrawParticles();
        DrawBirds();
    }
}
