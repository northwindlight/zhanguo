using Godot;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Zhanguo.Desktop.Layout;

public partial class WorldMap
{
    public int MarketCount=>_layout?.Regions.Sum(r=>r.TownLots.Count(l=>l.Kind=="market"))??0;
    public int RoadTiles=>_layout?.Roads.Count??0;
    public int VisibleWarArrows { get; private set; }
    public int VisibleLetters { get; private set; }
    public int VisibleBirds { get; private set; }
    public void PreviewBirds(float zoom)
    {
        float span=_size*256,t=(float)_time*.010f;
        var head=new Vector2(t*span*1.3f%(span+600)-300,t*span*.7f%(span+400)-200);
        _fit=false;_cameraAnchor=null;_scale=zoom;_offset=Size*.5f-head*_scale;UpdateView();
    }
    public Vector2? NationSceneAnchor(string actor)
    {
        if(_layout==null)return null;
        var hall=_layout.Regions.Where(r=>r.Input.Owner==actor).SelectMany(r=>r.Structures.Select(s=>(Region:r,Structure:s)))
            .Where(p=>p.Structure.Art.EndsWith("town-hall",StringComparison.Ordinal)&&p.Structure.Remaining==0)
            .OrderBy(p=>int.TryParse(p.Structure.Id,out int id)?id:int.MaxValue).FirstOrDefault();
        if(hall.Structure!=null)return new Vector2(hall.Region.Input.X*256+hall.Structure.Bounds.X+hall.Structure.Bounds.Width*.5f,
            hall.Region.Input.Y*256+hall.Structure.Bounds.Y+hall.Structure.Bounds.Height);
        return _homeAnchors.TryGetValue(actor,out var center)?center:null;
    }
    private Vector2? MarketAnchor(string actor)
    {
        if(_layout==null)return null;
        var market=_layout.Regions.Where(r=>r.Input.Owner==actor).SelectMany(r=>r.TownLots.Where(l=>l.Kind=="market").Select(l=>(Region:r,Lot:l)))
            .OrderBy(p=>p.Region.Structures.Where(s=>s.Art.EndsWith("town-hall",StringComparison.Ordinal)).Select(s=>int.TryParse(s.Id,out int id)?id:int.MaxValue).DefaultIfEmpty(int.MaxValue).Min()).FirstOrDefault();
        return market.Lot!=null?new Vector2(market.Region.Input.X*256+market.Lot.Bounds.X+market.Lot.Bounds.Width*.5f,
            market.Region.Input.Y*256+market.Lot.Bounds.Y+market.Lot.Bounds.Height):NationSceneAnchor(actor);
    }
    private Vector2? LetterAnchor(JsonObject letter,bool destination)
    {
        string actor=S(letter[destination?"target":"source"]);
        if(NationSceneAnchor(actor) is Vector2 real)return real;
        return letter[destination?"to":"from"] is JsonArray p?(new Vector2(I(p[0]),I(p[1]))+Vector2.One*.5f)*256:null;
    }
    private void DrawTrade()
    {
        if(_beat?["trade"] is not JsonObject trade)return;
        if(MarketAnchor(S(_beat["market_actor"])) is not Vector2 market)return;
        var at=Screen(market);var goods=trade["goods"]!.AsArray();
        string title=S(trade["side"])=="buy"?"买入":"卖出";
        string amounts=string.Join("  ",goods.Select(g=>$"{ResourceName(S(g!["resource"]))} {F(g["amount"]):+0.##;-0.##;0}"));
        double gold=F(trade["gold"]);
        string money=$"金币 {gold:+0.##;-0.##;0}";
        float width=Math.Max(184,Math.Max(_font.GetStringSize(title+"  "+amounts,fontSize:16).X,_font.GetStringSize(money,fontSize:16).X)+28);
        var obstacles=new List<Footprint>(_squadLabelBounds) { new(16,78,280,300),new(at.X-40,at.Y-52,80,66) };
        var placement=ResourceOverlayLayout.Place(new Footprint(at.X-width*.5f,at.Y-146,width,78),obstacles,Size.X,Size.Y,218,78);
        var rect=new Rect2(placement.X,placement.Y,placement.Width,placement.Height);
        DrawRect(rect,new Color(.045f,.08f,.08f,.95f));DrawRect(rect,new Color("a3a67d"),false,1);
        DrawString(_font,rect.Position+new Vector2(12,23),title+"  "+(amounts==""?"未产生成交":amounts),fontSize:16,
            modulate:new Color(S(trade["side"])=="buy"?"a2e4a6":"ffb49c"));
        DrawString(_font,rect.Position+new Vector2(12,52),money,fontSize:16,modulate:new Color(gold<0?"ff9891":"a2e4a6"));
    }
    private void DrawWarArrows()
    {
        VisibleWarArrows=0;
        if(_beat==null)return;
        JsonArray? approaches=_beat["approaches"]?.AsArray()??_beat["combat"]?["approaches"]?.AsArray();
        var routes=new List<(string Actor,Vector2 From,Vector2 To)>();
        if(approaches!=null)foreach(var approach in approaches)
        {
            var p=approach!["from"]!;var q=approach["to"]!;
            routes.Add((S(approach["actor"]),CellCenter(new Vector2I(I(p[0]),I(p[1]))),CellCenter(new Vector2I(I(q[0]),I(q[1])))));
        }
        if(_beat["combat"] is JsonObject combat&&_beat["position"] is JsonArray cell)
        {
            var owners=combat["before"]!.AsArray().Select(u=>S(u!["owner"])).Distinct().OrderBy(v=>v,StringComparer.Ordinal).ToArray();
            for(int i=0;i<owners.Length;i++)
                if(owners[i]!="野人"&&owners[i]!=S(combat["previous_owner"])&&!routes.Any(r=>r.Actor==owners[i]))
                {
                    var at=CellCenter(new Vector2I(I(cell[0]),I(cell[1])));
                    routes.Add((owners[i],at+new Vector2(i%2==0?-105:105,-35),at+new Vector2(i%2==0?-18:18,-35)));
                }
        }
        foreach(var route in routes)
        {
            var start=route.From;var target=route.To;
            if(start.DistanceTo(target)<10){start+=new Vector2(-100,-55);target+=new Vector2(-18,-55);}
            var direction=(target-start).Normalized();target-=direction*24;
            var color=NationColor(route.Actor);DrawLine(start,target,new Color(color,.83f),4);
            DrawSetTransform(target,direction.Angle(),Vector2.One*1.45f);
            UiArt.Draw(this,"war-arrow",new Vector2(-8,-8),new Vector2(16,16),color);DrawSetTransform(Vector2.Zero,0,Vector2.One);
            VisibleWarArrows++;
        }
    }
    private void DrawLetter()
    {
        VisibleLetters=0;
        if(_beat?["letter"] is not JsonObject letter||LetterAnchor(letter,false) is not Vector2 from||LetterAnchor(letter,true) is not Vector2 to)return;
        var a=Screen(from);var b=Screen(to);float t=Math.Clamp((_beatProgress-.1f)/.75f,0,1);t=t*t*(3-2*t);
        var position=a.Lerp(b,t)+new Vector2(0,-MathF.Sin(t*MathF.PI)*40);
        var color=NationColor(S(letter["source"]));
        for(int i=0;i<16;i+=2)DrawLine(a.Lerp(b,i/16f),a.Lerp(b,(i+1)/16f),new Color(color,.4f),2);
        DrawCircle(position,18,new Color(.04f,.08f,.07f,.78f));
        UiArt.Draw(this,"letter",position-new Vector2(15,12),new Vector2(30,24));
        DrawCircle(b,4,new Color(color,.6f+.4f*MathF.Sin(_beatProgress*MathF.PI)));VisibleLetters=1;
    }
}
