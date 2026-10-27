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
    private float _particleProgress;
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
        _beat=beat;_beatProgress=0;_battleStep=0;_particleProgress=0;
        if(beat["visual"] is JsonObject visual)SetFrame((JsonObject)visual.DeepClone());
        else {DesktopScene.Apply(Frame!,beat["delta"]!.AsObject());SetFrame(Frame!);}
        string phase=S(beat["phase"]);
        if(phase=="battle")_shakeAmp=1;
        else if(phase=="casualties")_shakeAmp=.45f;
        if(follow&&beat["position"] is JsonArray p)
        {
            _fit=false;
            _cameraScale=phase switch{
                "battle" or "casualties"=>1.55f,
                "build" or "demolish" or "complete" or "recruit" or "production" or "maintenance" or "occupation" or "trade"=>1.45f,
                "march"=>1.0f,
                "engage" or "arrival"=>1.25f,
                _=>1.15f};
            _cameraAnchor=FocusAnchor(phase,beat,I(p[0]),I(p[1]));
            UpdateView();
        }
        if(follow&&beat["letter"] is JsonObject letter&&LetterAnchor(letter,false) is Vector2 from&&LetterAnchor(letter,true) is Vector2 to)
        {
            _fit=false;_cameraAnchor=(from+to)*.5f;
            _cameraScale=Math.Clamp(Math.Min((Size.X-340)/(MathF.Abs(from.X-to.X)+256),(Size.Y-320)/(MathF.Abs(from.Y-to.Y)+256)),.04f,1.2f);
        }
        BeatBurst();
    }
    // Building actions frame the site itself, not just the tile center.
    private Vector2 FocusAnchor(string phase,JsonObject beat,int x,int y)
    {
        if(S(beat["market_actor"]) is {Length:>0} trader&&MarketAnchor(trader) is Vector2 market)return market;
        if(beat["ledger"]!=null&&int.TryParse(S(beat["anchor_building"]),out var hall))
        {
            var rect=BuildingRect(hall);
            if(rect.Size!=Vector2.Zero)return rect.GetCenter();
        }
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
    public void EndBeat() { _beat=null;_beatProgress=0;VisibleWarArrows=VisibleLetters=0;QueueRedraw(); }
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
        return _layout!.CanBuild(r.Input,body)&&!r.Structures.Any(b=>RegionLayout.StructureVisualBounds(b).Overlaps(body,3))&&!r.TownLots.Any(l=>RegionLayout.TownVisualBounds(l).Overlaps(body,3))&&!r.OilPatches.Any(p=>p.Overlaps(body,2))&&
            !r.Gold.Any(g=>RegionLayout.GoldBounds(g).Overlaps(body,2))&&!r.Iron.Any(g=>RegionLayout.RockBounds(g).Overlaps(body,2))&&
            !r.Gold.Concat(r.Iron).Any(g=>_layout.ResourceVisualBounds(r.Input,g).Overlaps(body,2))&&
            !r.Trees.Any(t=>RegionLayout.TreeBounds(t.Foot).Overlaps(body,1))&&
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

    // Unified actors use source anchors, action rows and exact cloth slots.
    // Wild guards retain a small procedural figure until their sheet is drawn.
    private static readonly string[] Skins={"e8c39a","d9a878","c68e5f","b57c4e"};
    private static readonly string[] Hairs={"3a2e24","54432f","777770","2c2c31"};
    private void MiniPerson(Vector2 foot,Color cloth,float size,double clock,bool moving=false,bool soldier=false,bool horse=false,bool working=false,float alpha=1,bool fighting=false,bool wild=false,bool facingRight=true,int seed=0,int directionOverride=-1)
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
        if(!wild)
        {
            string role=horse?"cavalry":soldier?seed%3==0?"spear":seed%3==1?"sword":"axe":working?"worker":"resident";
            var e=UnifiedArt.Get("actor-"+role);
            bool act=fighting||working;
            int frames=act?e.Act:moving?e.Walk:e.Idle;
            int frame=(int)(Math.Max(0,clock)*(act?7:moving?8:2))%frames;
            if(fighting&&S(_beat?["phase"])=="battle")frame=Math.Clamp((int)(SquadView.AttackPhase(_beatProgress+(seed%3-1)*.006f)*frames),0,frames-1);
            int direction=directionOverride>=0?directionOverride:facingRight?1:3;
            if(directionOverride<0&&!soldier&&!moving&&!working)direction=(seed>>3)%4;
            int row=(act?8:moving?4:0)+direction;
            var at=(foot-e.Anchor*s).Round();
            DrawTextureRectRegion(UnifiedArt.Actor(role,cloth),new Rect2(at,(Vector2)e.Cell*s),
                new Rect2(new Vector2(frame*e.Cell.X,row*e.Cell.Y),e.Cell),new Color(1,1,1,alpha));
            if(!soldier&&moving&&seed%3<2)
            {
                var item=(foot+new Vector2(facingRight?3:-6,-6)*s).Round();
                DrawRect(new Rect2(item,new Vector2(3,3)*s),new Color(seed%3==0?"ae8757":"d3b965"));
            }
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
            if(r.Input.Frozen)continue;
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
                int role=i+r.Input.X*17+r.Input.Y*29;
                string[] clothes={"c4ac79","8ca7a0","b58f7c","9793b4","b8b888","ccae95"};
                MiniPerson(p,new Color(clothes[Math.Abs(role)%clothes.Length]),_scale,_time+i,moving,working:!moving&&role%6==0,seed:role,directionOverride:moving?Direction((walks[i].B-walks[i].A)*(t<.55f?1:-1)):-1);VisibleResidents++;
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
    private static int Direction(Vector2 delta)=>Math.Abs(delta.Y)>Math.Abs(delta.X)?delta.Y>0?0:2:delta.X>=0?1:3;
    private int MarchDirection(JsonNode unit)
    {
        var motion=_beat?["motions"]?.AsArray().FirstOrDefault(m=>S(m!["unit"])==DesktopScene.Key("armies",unit));
        if(motion?["points"] is not JsonArray points||points.Count<2)return -1;
        int index=Math.Min((int)(_beatProgress*(points.Count-1)),points.Count-2);
        return Direction(new Vector2(I(points[index+1]![0])-I(points[index]![0]),I(points[index+1]![1])-I(points[index]![1])));
    }
    private Vector2 MotionWorld(JsonNode motion)
    {
        var points=motion["points"]!.AsArray();
        float t=_beatProgress*(points.Count-1);int i=Math.Min((int)t,points.Count-2);
        var a=new Vector2(I(points[i]![0]),I(points[i]![1]));
        var b=new Vector2(I(points[i+1]![0]),I(points[i+1]![1]));
        return (a.Lerp(b,Math.Clamp(t-i,0,1))+Vector2.One*.5f)*256;
    }
    private (RegionPlan Region,Structure Building)? FindBuilding(int id)
    {
        string wanted=$"b/{id:D10}";
        foreach(var r in _layout!.Regions)
            foreach(var b in r.Structures)if(b.Id==wanted)return (r,b);
        // Dense regions aggregate visual buildings. Pops still use each real
        // building id, so route them to the matching retained cluster.
        var actual=Frame?["scene"]?["buildings"]?.AsArray().FirstOrDefault(b=>I(b!["id"])==id);
        if(actual==null)return null;
        var region=_layout.Regions.FirstOrDefault(r=>r.Input.X==I(actual["x"])&&r.Input.Y==I(actual["y"]));
        var group=region?.Structures.FirstOrDefault(s=>s.Art==S(actual["kind"]))??region?.Structures.FirstOrDefault(s=>s.Count>1);
        return region!=null&&group!=null?(region,group):null;
    }
    private Vector2 BuildingAnchor(int id)
    {
        var rect=BuildingRect(id);
        return rect.Size==Vector2.Zero?Vector2.Zero:rect.Position+new Vector2(rect.Size.X*.5f,-16);
    }
    private Rect2 BuildingRect(int id)
    {
        if(FindBuilding(id) is not {} found)return new Rect2();
        var r=found.Region;var b=found.Building;
        return new Rect2(new Vector2(r.Input.X*256+b.Bounds.X,r.Input.Y*256+b.Bounds.Y),new Vector2(b.Bounds.Width,b.Bounds.Height));
    }
    private string ResourceName(string id)=>id switch {"native:gold"=>"金","native:food"=>"粮","native:wood"=>"木","native:ore"=>"矿","native:oil"=>"油","native:equipment"=>"装备","native:supply"=>"补给","hp"=>"HP",_=>id};
    private void ResourceIcon(Vector2 p,string resource,float alpha)
    {
        Color C(string hex)=>new Color(new Color(hex),alpha);
        switch(resource)
        {
            case "native:gold":DrawCircle(p,5,C("d9bd66"));DrawCircle(p,3,C("f1d999"));break;
            case "native:ore":DrawColoredPolygon(new[]{p+new Vector2(-5,3),p+new Vector2(-3,-4),p+new Vector2(3,-5),p+new Vector2(6,1),p+new Vector2(2,5)},C("9faeaa"));DrawLine(p+new Vector2(-2,-2),p+new Vector2(2,2),C("d4dcd0"),1);break;
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
        _resourcePopBounds.Clear();
        if(_beat==null)return;
        DrawWarArrows();DrawTrade();DrawLetter();
        var popupBounds=new List<Footprint>(_squadLabelBounds);
        popupBounds.AddRange(HudObstacles.Select(p=>p with { Height=p.Height+35 }));
        if(_beat["pops"] is JsonArray pops)
            for(int i=0;i<pops.Count;i++)
            {
                var pop=pops[i]!;Vector2 anchor;
                if(pop["building"]!=null)anchor=BuildingAnchor(I(pop["building"]));
                else {var p=pop["position"]!;anchor=(new Vector2(I(p[0]),I(p[1]))+Vector2.One*.5f)*256;}
                float alpha=Math.Min(1,(1-_beatProgress)*4);double amount=F(pop["amount"]);
                string resource=S(pop["resource"]),number=pop["amount"]==null?"":$"{(amount>0?"+":"")}{amount:G4}";
                string text=ResourceName(resource)+number;
                float width=_font.GetStringSize(text,fontSize:16).X+26;
                var baseAt=Screen(anchor)+new Vector2(-width*.5f,-35);
                var rect=ResourceOverlayLayout.Place(new Footprint(baseAt.X,baseAt.Y,width,23),popupBounds,Size.X,Size.Y,265);
                popupBounds.Add(rect);
                _resourcePopBounds.Add(rect with { Y=rect.Y-_beatProgress*35 });
                var at=new Vector2(rect.X+18,rect.Y+17-_beatProgress*35);
                var color=new Color(new Color(amount<0?"efbc80":"c9efa3"),alpha);
                DrawRect(new Rect2(new Vector2(rect.X,rect.Y-_beatProgress*35),new Vector2(rect.Width,rect.Height)),new Color(.045f,.075f,.08f,.80f*alpha));
                ResourceIcon(at-new Vector2(10,5),resource,alpha);
                DrawString(_font,at,text,fontSize:16,modulate:color);
            }
        string phase=S(_beat["phase"]);
        if(phase is "battle" or "casualties" && _beat["position"] is JsonArray cell)
        {
            var at=CellCenter(new Vector2I(I(cell[0]),I(cell[1])));
            if(phase=="battle")
                for(int i=0;i<4;i++)
                {
                    float t=SquadView.Strike(_beatProgress);
                    if(t<.55f)continue;
                    var p=at+new Vector2((float)Math.Sin(i*3)*17,(float)Math.Cos(i*3)*12);
                    DrawLine(p+new Vector2(-5,-6)*t,p+new Vector2(5,6)*t,new Color(1,.9f,.64f,(t-.55f)*1.6f),2);
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
                    var unit=fallen[i/8];
                    var color=S(unit!["owner"])=="野人"?new Color("977553"):NationColor(S(unit["owner"]));
                    DrawSetTransform(p,Mathf.Pi*.5f,new Vector2(.7f,.7f));
                    MiniPerson(Vector2.Zero,color,1,0,soldier:true,horse:S(unit["kind"]).EndsWith("cavalry"),alpha:fade,wild:S(unit["owner"])=="野人",seed:i);
                    DrawSetTransform(Vector2.Zero);
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
            case "demolish":
                for(int i=0;i<12;i++)Spawn(new Particle{Pos=at+new Vector2(i*5-28,(i*7%11)-3),Vel=new Vector2((i%3-1)*12,-8-i%4),Life=1.1f,Max=1.1f,Size=2.2f,Tint=new Color("b8aa8a"),Kind=KindDust});
                for(int i=0;i<6;i++)Spawn(new Particle{Pos=at+new Vector2(i*8-20,1),Vel=new Vector2((i%3-1)*16,-20),Life=.65f,Max=.65f,Size=1.5f,Tint=new Color("8d8166"),Kind=KindDebris});
                break;
        }
    }
    private void AmbientTick()
    {
        float dt=1f/24f;
        bool combatClock=S(_beat?["phase"]) is "battle" or "casualties";
        if(combatClock)
        {
            dt=Math.Max(0,_beatProgress-_particleProgress)*(float)F(_beat!["duration"]);
            _particleProgress=_beatProgress;
        }
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
            if(r.Input.Frozen)continue;
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
            if(phase=="battle"&&_beatProgress<.96f)
            {
                int step=(int)Math.Floor(SquadView.DamageProgress(_beatProgress)*3+.001f);
                if(step!=_battleStep)
                {
                    _battleStep=step;
                    for(int i=0;i<18;i++)
                    {
                        var at=center+new Vector2((i%3-1)*4,(i/3)*7-22);
                        Spawn(new Particle{Pos=at,Vel=new Vector2((i%5-2)*17,-20-i%4*7),Life=.25f,Max=.25f,Size=1.2f,Tint=new Color("ffe9a0"),Kind=KindSpark});
                    }
                    for(int i=0;i<6;i++)Spawn(new Particle{Pos=center+new Vector2(i*7-17,(i*7%5)-2),Vel=new Vector2((i%3-1)*10,-8),Life=.45f,Max=.45f,Size=1.8f,Tint=new Color("b0a488"),Kind=KindDust});
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
    private void DrawBirds()
    {
        VisibleBirds=0;
        float span=_size*256;
        for(int flock=0;flock<2;flock++)
        {
            float t=(float)_time*(.010f+flock*.004f)+flock*.53f;
            var head=new Vector2((t*span*1.3f+flock*900)%(span+600)-300,(t*span*.7f+flock*2000)%(span+400)-200);
            for(int i=0;i<6;i++)
            {
                var world=head+new Vector2(-i*18-(i/3)*8,(i%3)*9);
                if(PlayerMode&&(!IsLivePoint(world)||!IsLivePoint(world+new Vector2(32,32)/_scale)||!IsLivePoint(world-new Vector2(32,32)/_scale)))continue;
                var at=Screen(world);
                if(!OnScreen(at))continue;
                bool up=MathF.Sin((float)_time*9+i*1.7f+flock)>0;
                float width=Math.Clamp(6+_scale*8,6,24);
                UiArt.Draw(this,up?"bird-up":"bird-down",at-new Vector2(width*.5f,width*.25f),new Vector2(width,width*.5f));
                VisibleBirds++;
            }
        }
    }
    private void DrawParticles()
    {
        foreach(var p in _particles)
        {
            if(PlayerMode&&!IsLivePoint(p.Pos))continue;
            var at=Screen(p.Pos);
            if(!OnScreen(at))continue;
            float t=1-p.Life/p.Max;
            switch(p.Kind)
            {
                case KindDust:
                    DrawRect(new Rect2(at.Round(),Vector2.One*Math.Max(1,MathF.Round(p.Size*(.6f+t*1.4f)*_scale))),new Color(p.Tint,(1-t)*.34f));
                    break;
                case KindSmoke:
                {
                    float r=p.Size*(1+t*3.2f)*Math.Max(.6f,_scale);
                    DrawRect(new Rect2(at.Round(),Vector2.One*Math.Max(1,MathF.Round(r))),new Color(p.Tint,(1-t)*.30f));
                    DrawRect(new Rect2((at+new Vector2(r*.3f,-r*.25f)).Round(),Vector2.One*Math.Max(1,MathF.Round(r*.62f))),new Color(p.Tint,(1-t)*.22f));
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
        DrawParticles();
        DrawBirds();
    }
}
