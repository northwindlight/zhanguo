using Godot;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Zhanguo.Desktop.Layout;

public partial class WorldMap
{
    private sealed record SquadLabel(SquadView.Status Status,Vector2 At,Color Color,float FigureBottom,float Side,bool Separate,float FigureWidth=60,float FigureTop=-30);
    private readonly List<SquadLabel> _squadLabels=new();
    public int VisibleSquadBars { get; private set; }
    public int SelectedSquadUnits { get; private set; }
    public IReadOnlyList<string> LastSquadLabels=>_squadLabelText;
    private readonly List<string> _squadLabelText=new();
    private readonly Dictionary<string,StyleBoxFlat> _squadLabelStyles=new();
    private readonly List<Footprint> _squadLabelBounds=new();
    public IReadOnlyList<Footprint> SquadLabelBounds=>_squadLabelBounds;
    private readonly List<Footprint> _resourcePopBounds=new();
    public IReadOnlyList<Footprint> ResourcePopBounds=>_resourcePopBounds;
    private readonly Dictionary<string,int> _wildFigureCounts=new();
    public IReadOnlyDictionary<string,int> WildFigureCounts=>_wildFigureCounts;

    private void DrawTroops()
    {
        VisibleSoldiers=VisibleSquadBars=SelectedSquadUnits=0;_squadLabels.Clear();_squadLabelText.Clear();_squadLabelBounds.Clear();_wildFigureCounts.Clear();
        if(Frame==null||_layout==null)return;
        if(!Units&&!ShowGuards)return;
        string Route(JsonNode u)=>_beat?["motions"]?.AsArray().FirstOrDefault(m=>m!["unit"]!.ToString()==DesktopScene.Key("armies",u))?["points"]?.ToJsonString()??"";
        var groups=SquadView.Units(Frame).Where(u=>!PlayerMode||IsLive(new(I(u["x"]),I(u["y"]))))
            .GroupBy(u=>(X:I(u!["x"]),Y:I(u["y"]),Owner:S(u["owner"]),Kind:S(u["kind"]),Path:Route(u),Chosen:PlayerMode&&S(u["owner"])==PlayerActor&&_selectedArmyIds.Contains(I(u["id"]))))
            .OrderBy(g=>g.Key.Y).ThenBy(g=>g.Key.X).ThenBy(g=>g.Key.Owner,StringComparer.Ordinal).ThenBy(g=>g.Key.Kind,StringComparer.Ordinal).ToArray();
        var cells=groups.GroupBy(g=>(g.Key.X,g.Key.Y)).ToDictionary(g=>g.Key,g=>g.ToArray());
        foreach(var group in groups)
        {
            var first=group.First()!;bool wild=group.Key.Owner=="野人";
            if(wild&&!ShowGuards||!wild&&!Units)continue;
            var local=cells[(group.Key.X,group.Key.Y)];
            bool moving=S(_beat?["phase"])=="march"&&_beat?["motions"]?.AsArray().Any(m=>m!["unit"]!.ToString()==DesktopScene.Key("armies",first))==true;
            var guard=local.FirstOrDefault(g=>g.Key.Owner=="野人")?.First();
            var anchor=guard!=null&&!moving?GuardAnchor(guard):TroopPosition(first);
            var at=Screen(anchor);if(!OnScreen(at,160))continue;
            var color=wild?new Color("ac8b63"):NationColor(group.Key.Owner);
            if(group.Key.Chosen)SelectedSquadUnits+=group.Count();
            float size=_scale;
            if(wild&&size<.3f)
            {
                DrawRect(new Rect2(at-new Vector2(3,2),new Vector2(6,4)),new Color("896f4a"));
                DrawLine(at+new Vector2(-3,-2),at+new Vector2(0,-5),new Color("c2aa7c"),1);continue;
            }
            bool horse=group.Key.Kind.EndsWith("cavalry",StringComparison.Ordinal);
            var owners=local.Select(g=>g.Key.Owner).Distinct().OrderBy(s=>s,StringComparer.Ordinal).ToArray();
            int ownerIndex=Array.IndexOf(owners,group.Key.Owner);float side=ownerIndex%2==0?-1:1;
            var same=local.Where(g=>g.Key.Owner==group.Key.Owner).ToArray();
            int slot=Array.IndexOf(same,group);
            int count=DesktopScene.SquadFigures(group.Count()),columns=count<=16?4:count<=32?6:8;
            if(_scale<.3f)count=Math.Min(count,8);
            if(wild)foreach(var u in group)_wildFigureCounts[DesktopScene.Key("armies",u)]=count;
            float spacingX=horse?14:9,spacingY=horse?17:12;
            float rows=MathF.Ceiling(count/(float)columns),formationWidth=(columns-1)*spacingX;
            float lane=(slot-(same.Length-1)*.5f)*(rows*spacingY+78)*size;
            at.Y+=lane+ownerIndex/2*100*size;
            bool atBattle=_beat?["position"] is JsonArray bp&&I(bp[0])==group.Key.X&&I(bp[1])==group.Key.Y;
            bool fighting=S(_beat?["phase"])=="battle"&&atBattle&&
                (_beat?["combat"]?["enemies"]?[group.Key.Owner] is JsonArray enemies&&enemies.Count>0);
            float strike=fighting?SquadView.Strike(_beatProgress):0;
            if(owners.Length>1)
            {
                float advance=fighting?Math.Clamp(_beatProgress/.20f,0,1):0;
                advance=advance*advance*(3-2*advance);
                // Contact edges meet; rear ranks stay in formation. Cavalry
                // gets enough width for horses instead of stacking over infantry.
                float contact=formationWidth*.5f+10;
                float gap=contact+26*(1-advance)-strike*(horse?8:5);
                at.X+=side*gap*size;
                if(fighting)at.Y-=lane*advance*.3f;
                if(fighting&&_beat?["combat"] is JsonObject detail)
                {
                    var before=detail["before"]!.AsArray().Where(u=>S(u!["owner"])==group.Key.Owner).Sum(u=>F(u!["hp"]));
                    var after=detail["after"]!.AsArray().Where(u=>S(u!["owner"])==group.Key.Owner).Sum(u=>F(u!["hp"]));
                    if(after<before)at.X+=side*strike*2*size;
                }
            }
            bool mustering=!moving&&!fighting&&S(_beat?["phase"])=="recruit"&&atBattle;
            double clock=fighting?_beatProgress*4.6:moving?_beatProgress*4:_time;
            float muster=Math.Clamp(_beatProgress*1.5f,0,1);muster=muster*muster*(3-2*muster);
            var musterOrigin=Screen((new Vector2(group.Key.X,group.Key.Y)+Vector2.One*.5f)*256);
            for(int i=0;i<count;i++)
            {
                var spot=at+new Vector2((i%columns-(columns-1)*.5f)*spacingX,i/columns*spacingY-12)*size;
                bool front=side<0?i%columns==columns-1:i%columns==0;
                if(fighting&&front)spot.X-=side*strike*(horse?4:2)*size;
                var p=mustering?musterOrigin.Lerp(spot,muster):spot;
                if(group.Key.Chosen)
                {
                    float radius=Math.Max(3.5f,(horse?8:5)*size);
                    DrawCircle(p+new Vector2(0,3)*size,radius,new Color(.8f,.96f,.6f,.18f));
                    DrawArc(p+new Vector2(0,3)*size,radius,0,MathF.Tau,18,new Color("ffe29a"),Math.Max(1,size));
                }
                bool stepping=moving||mustering&&muster<.999f||fighting&&_beatProgress<.20f;
                if(wild&&owners.Length==1&&((clock+i*.31)/7)%1<.34)
                {stepping=true;p+=new Vector2(MathF.Sin((float)clock+i)*2,MathF.Cos((float)clock+i)*1.5f)*size;}
                bool face=mustering?spot.X>=musterOrigin.X:owners.Length>1?side<0:i%7!=0;
                MiniPerson(p,color,size,clock+i*.071,stepping,soldier:true,horse:horse,fighting:fighting&&strike>.12f,wild:wild,facingRight:face,seed:i,directionOverride:moving?MarchDirection(first):-1);
                if(fighting&&front&&strike>.55f)
                {
                    var blade=p+new Vector2((side<0?1:-1)*(horse?12:7),-9)*size;
                    DrawArc(blade,5*size,side<0?-.7f:2.4f,side<0?.7f:3.8f,7,new Color(1,.9f,.65f,(strike-.55f)*1.6f),Math.Max(1,size));
                }
                if(moving&&horse&&i%3==0)
                    DrawCircle(p+new Vector2(-6,3)*size,2*size,new Color(.65f,.58f,.42f,.25f));
                VisibleSoldiers++;
            }
            if(!wild)DrawSquadFlag(at+new Vector2(-formationWidth*.5f-5,-22)*size,color,size);
            if(group.Key.Chosen)
            {
                int total=SquadView.Units(Frame).Count(u=>S(u["owner"])==group.Key.Owner&&S(u["kind"])==group.Key.Kind&&I(u["x"])==group.Key.X&&I(u["y"])==group.Key.Y);
                DrawString(_font,at+new Vector2(-28,-40)*size,$"已选{group.Count()}/{total}",fontSize:14,modulate:new Color("ffe29a"));
            }
            bool focus=at.DistanceTo(Size*.5f)<110*Math.Max(.8f,_scale)||Selected==new Vector2I(group.Key.X,group.Key.Y);
            if(_scale>=.48f&&OnScreen(at,20)&&(!wild||fighting||owners.Length>1||focus&&_scale>=.72f))
            {
                var status=SquadView.Summarize(Frame,group.Select(u=>u!),fighting?_beat?["combat"] as JsonObject:null,_beatProgress);
                bool separate=owners.Length>1||same.Length>1;
                _squadLabels.Add(new(status,at,color,(rows-1)*spacingY*size+6*size,separate?(owners.Length>1?side:-1):0,separate,
                    (formationWidth+(horse?24:16)+10)*size,-(12+(horse?24:16))*size));
            }
        }
        DrawSquadLabels();
    }
    private void DrawSquadFlag(Vector2 pole,Color color,float size)
    {
        DrawLine(pole,pole+new Vector2(0,17)*size,new Color("d4cab1"),Math.Max(1,size));
        float wave=MathF.Sin((float)_time*6+pole.X*.1f)*size;
        DrawColoredPolygon(new[]{pole,pole+new Vector2(12*size,wave),pole+new Vector2(11*size,7*size+wave),pole+new Vector2(0,7*size)},color);
        DrawLine(pole,pole+new Vector2(12*size,wave),new Color("f4e4b8"),Math.Max(.7f,size*.6f));
    }
    private void DrawSquadLabels()
    {
        var occupied=_squadLabels.Select(l=>new Footprint(l.At.X-42,l.At.Y+l.FigureBottom,84,25)).ToList();
        occupied.AddRange(_squadLabels.Select(l=>new Footprint(l.At.X-l.FigureWidth*.5f,l.At.Y+l.FigureTop,l.FigureWidth,l.FigureBottom-l.FigureTop)));
        occupied.AddRange(HudObstacles);
        foreach(var label in _squadLabels.OrderBy(l=>l.At.Y))
        {
            var s=label.Status;bool detail=_scale>=.72f;
            const int font=16;
            string name=s.Name.Length>3?s.Name[..3]+"…":s.Name;
            float width=Math.Clamp(_font.GetStringSize(name+s.Health,fontSize:font).X+42,152,240),height=detail?64:39;
            var center=label.At+new Vector2(label.Separate?label.Side*(width*.5f+30):0,-44);
            // Check collisions after viewport clamping as well: two cards near
            // the banner must not collapse onto the same upper screen edge.
            var placed=ResourceOverlayLayout.Place(new Footprint(center.X-width*.5f,center.Y-height,width,height),occupied,Size.X,Size.Y,230,72);
            if(HudObstacles.Any(p=>p.Overlaps(placed,1)))continue;
            var box=new Rect2(placed.X,placed.Y,placed.Width,placed.Height);
            occupied.Add(placed);
            _squadLabelBounds.Add(new Footprint(box.Position.X,box.Position.Y,box.Size.X,box.Size.Y+35));
            if(!_squadLabelStyles.TryGetValue(s.Owner,out var style))
            {
                style=new StyleBoxFlat { BgColor=new Color(.045f,.075f,.08f,.91f),BorderColor=new Color(label.Color,.65f) };
                style.SetBorderWidthAll(1);style.SetCornerRadiusAll(4);_squadLabelStyles[s.Owner]=style;
            }
            DrawStyleBox(style,box);
            if(label.Separate)
            {
                var from=new Vector2(label.Side<0?box.End.X:box.Position.X,box.Position.Y+box.Size.Y*.6f);
                DrawLine(from,label.At+new Vector2(0,-24),new Color(label.Color,.5f),1);
            }
            var start=box.Position+new Vector2(8,19);
            DrawString(_font,start,name,fontSize:font,modulate:label.Color.Lightened(.24f));
            float nameW=_font.GetStringSize(name,fontSize:font).X;
            ResourceIcon(start+new Vector2(nameW+11,-6),"hp",1);
            DrawString(_font,start+new Vector2(nameW+23,0),s.Health,fontSize:font,modulate:new Color("f3ead0"));
            var bar=new Rect2(box.Position+new Vector2(8,26),new Vector2(width-16,5));
            DrawRect(bar,new Color("3f4540"));
            DrawRect(new Rect2(bar.Position,new Vector2(bar.Size.X*s.Fraction,bar.Size.Y)),s.Fraction<.3f?new Color("d68172"):s.Fraction<.65f?new Color("d1b671"):new Color("91bf87"));
            if(detail)
            {
                var shield=box.Position+new Vector2(13,48);
                DrawColoredPolygon(new[]{shield+new Vector2(-5,-5),shield+new Vector2(5,-5),shield+new Vector2(4,1),shield+new Vector2(0,5),shield+new Vector2(-4,1)},new Color("a2bac5"));
                DrawString(_font,shield+new Vector2(12,4),s.DefenseText,fontSize:16,modulate:new Color("b8cad0"));
            }
            var strength=label.At+new Vector2(0,label.FigureBottom+15);
            var textSize=_font.GetStringSize(s.Strength,fontSize:font);
            DrawRect(new Rect2(strength-new Vector2(textSize.X*.5f+5,15),new Vector2(textSize.X+10,20)),new Color(.045f,.075f,.08f,.88f));
            DrawString(_font,strength-new Vector2(textSize.X*.5f,0),s.Strength,fontSize:font,modulate:new Color("f4e4b8"));
            VisibleSquadBars++;_squadLabelText.Add($"{s.Name} ♥ {s.Health}\n盾 {s.DefenseText}\n{s.Strength}");
        }
    }
}
