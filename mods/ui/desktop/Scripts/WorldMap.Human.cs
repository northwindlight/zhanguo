using Godot;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;

public partial class WorldMap
{
    public bool PlayerMode=>Frame?["player_actor"]!=null;
    public string PlayerActor=>S(Frame?["player_actor"]);
    public bool PlayerPanelOpen;
    private readonly Dictionary<Vector2I,JsonObject> _humanTargets=new();
    private string _humanTargetMode="";
    public Action<JsonObject?>? HumanTargetHovered;
    public int TargetCount=>_humanTargets.Count;
    public int UnknownCount=>_tiles.Values.Count(t=>S(t["visibility"])=="unknown");
    public int MemoryCount=>_tiles.Values.Count(t=>S(t["visibility"])=="memory");
    public bool IsLive(Vector2I p)=>!PlayerMode||S(_tiles.GetValueOrDefault(p)?["visibility"])=="live";
    private bool IsLivePoint(Vector2 p)=>IsLive(new((int)MathF.Floor(p.X/256),(int)MathF.Floor(p.Y/256)));
    public void SetHumanTargets(string mode,JsonArray? rows)
    {
        _humanTargetMode=mode;_humanTargets.Clear();
        if(rows!=null)foreach(var row in rows)
            _humanTargets[new(I(row!["position"]![0]),I(row["position"]![1]))]=row.AsObject();
        QueueRedraw();
    }
    public JsonObject? HumanTarget(Vector2I at)=>_humanTargets.GetValueOrDefault(at);
    public void SelectAndFocus(Vector2I at)
    {
        Selected=at;_fit=false;_scale=Math.Clamp(Math.Min((Size.X-560)/256f,(Size.Y-300)/256f),.45f,1.5f);
        var focus=new Vector2(Math.Max(340,(Size.X-540)*.5f),Size.Y*.55f);
        _offset=focus-(new Vector2(at.X,at.Y)+Vector2.One*.5f)*Cell*_scale;_cameraAnchor=null;UpdateView();
    }
    public void FocusHumanTargets()
    {
        if(_humanTargets.Count==0)return;
        var points=_humanTargets.Keys.ToArray();
        float left=points.Min(p=>p.X),right=points.Max(p=>p.X)+1,top=points.Min(p=>p.Y),bottom=points.Max(p=>p.Y)+1;
        var free=new Rect2(312,216,Math.Max(160,Size.X-312-(PlayerPanelOpen?552:16)),Math.Max(180,Size.Y-300));
        _scale=Math.Clamp(Math.Min(free.Size.X/((right-left)*256),free.Size.Y/((bottom-top)*256)),.04f,1.5f);
        _offset=free.GetCenter()-new Vector2((left+right)*128,(top+bottom)*128)*_scale;_fit=false;_cameraAnchor=null;UpdateView();
    }
    private void HoverHumanTarget(Vector2 at)
    {
        if(_humanTargetMode=="")return;
        var p=(at-_offset)/(_scale*Cell);
        HumanTargetHovered?.Invoke(HumanTarget(new((int)MathF.Floor(p.X),(int)MathF.Floor(p.Y))));
    }
    private void DrawHumanFog()
    {
        if(!PlayerMode)return;
        DrawSetTransform(_offset+_jitter,0,Vector2.One*_scale);
        foreach(var (p,tile) in _tiles)
        {
            string state=S(tile["visibility"]);if(state=="live")continue;
            var rect=new Rect2(new Vector2(p.X,p.Y)*Cell,Vector2.One*Cell);
            if(!new Rect2(Vector2.Zero,Size).Intersects(new Rect2(_offset+rect.Position*_scale,rect.Size*_scale)))continue;
            if(state=="unknown")DrawTextureRect(HumanArt.Texture("fog-unknown"),rect,true);
            else DrawTextureRect(HumanArt.Texture("fog-memory"),rect,true);
        }
        foreach(var (p,tile) in _tiles)
        {
            if(S(tile["visibility"])=="unknown")continue;
            int mask=0;var neighbors=new[]{new Vector2I(0,-1),new Vector2I(1,0),new Vector2I(0,1),new Vector2I(-1,0)};
            for(int i=0;i<4;i++)if(_tiles.TryGetValue(p+neighbors[i],out var other)&&S(other["visibility"])=="unknown")mask|=1<<i;
            if(mask>0)DrawTextureRect(HumanArt.Texture($"fog-edge-{mask:00}"),new Rect2(new Vector2(p.X,p.Y)*Cell,Vector2.One*Cell),false);
        }
        if(Selected.X>=0)DrawTextureRect(HumanArt.Texture("tile-selected"),new Rect2(new Vector2(Selected.X,Selected.Y)*Cell,Vector2.One*Cell),false);
        foreach(var (p,target) in _humanTargets)
        {
            string mode=_humanTargetMode=="attack"?"attack":"move";
            string variant=target["uncertain"]?.GetValue<bool>()==true?"uncertain":"known";
            DrawTextureRect(HumanArt.Texture($"range-{mode}-{variant}"),new Rect2(new Vector2(p.X,p.Y)*Cell,Vector2.One*Cell),false);
        }
        DrawSetTransform(Vector2.Zero);
        foreach(var group in Frame!["armies"]!.AsArray().Where(u=>S(u!["owner"])==PlayerActor&&!IsLive(new(I(u["x"]),I(u["y"]))))
            .GroupBy(u=>(X:I(u!["x"]),Y:I(u["y"]))))
        {
            var at=CellCenter(new(group.Key.X,group.Key.Y));
            if(!OnScreen(at))continue;
            var tint=NationColor(PlayerActor);
            DrawTextureRect(HumanArt.Texture("army-own"),new Rect2(at-new Vector2(16,24),new Vector2(32,32)),false,tint);
            string text=$"己方 {group.Count()} 支 · {group.Sum(u=>F(u!["hp"])):G5}HP";
            var width=_font.GetStringSize(text,fontSize:16).X;
            DrawRect(new Rect2(at+new Vector2(-width/2-4,12),new Vector2(width+8,22)),new Color("172723"));
            DrawString(_font,at+new Vector2(-width/2,29),text,fontSize:16,modulate:new Color("e8dfbe"));
        }
    }
}
