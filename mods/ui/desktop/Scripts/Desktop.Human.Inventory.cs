using Godot;
using System;
using System.Linq;
using System.Text.Json.Nodes;
using static WorldMap;

public partial class Desktop
{
    private bool HasRecruitFacility(JsonObject tile)
    {
        var kinds=HumanFacts["building_catalog"]!.AsArray().Where(b=>b!["roles"]!.AsArray().Any(r=>S(r) is "recruiter" or "militia")).Select(b=>S(b!["id"])).ToHashSet();
        return tile["building_instances"]!.AsArray().Any(b=>kinds.Contains(S(b!["kind"]))&&I(b["remaining"])==0);
    }
    private void RenderHumanInventory(JsonObject tile)
    {
        var buildings=tile["building_instances"]?.AsArray()??new JsonArray();
        HumanText("地块建筑",true);
        if(buildings.Count==0){HumanText("这里未记录建筑。",color:"a7b8a9");return;}
        var grid=new GridContainer { Columns=3,SizeFlagsHorizontal=SizeFlags.ExpandFill };grid.AddThemeConstantOverride("h_separation",6);grid.AddThemeConstantOverride("v_separation",6);_humanContent.AddChild(grid);
        foreach(var group in buildings.GroupBy(b=>S(b!["kind"])))
        {
            string kind=group.Key;int pending=group.Count(b=>I(b!["remaining"])>0);
            string name=HumanLabel(kind);
            if(kind=="native:castle")
            {
                var complete=group.Where(b=>I(b!["remaining"])==0).ToArray();
                name+=group.All(b=>b!["level_known"]?.GetValue<bool>()!=false)?$" · {(complete.Length==0?0:complete.Max(b=>I(b!["level"])))}级":" · 等级未知";
            }
            else name+=$" ×{group.Count()}";
            var card=new Button { CustomMinimumSize=new Vector2(126,116),SizeFlagsHorizontal=SizeFlags.ExpandFill,FocusMode=FocusModeEnum.None,
                TooltipText=name+"\n"+S(_map.Frame!["building_help"]?[kind])+(pending>0?$"\n{pending} 项施工中":""),MouseDefaultCursorShape=CursorShape.Help };
            HumanArt.Style(card);grid.AddChild(card);
            var body=new VBoxContainer { MouseFilter=MouseFilterEnum.Ignore };body.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);body.OffsetLeft=5;body.OffsetTop=5;body.OffsetRight=-5;body.OffsetBottom=-5;card.AddChild(body);
            body.AddChild(new TextureRect { Texture=RtsArt.Building(kind),CustomMinimumSize=new Vector2(0,62),ExpandMode=TextureRect.ExpandModeEnum.IgnoreSize,
                StretchMode=TextureRect.StretchModeEnum.KeepAspectCentered,MouseFilter=MouseFilterEnum.Ignore,TextureFilter=TextureFilterEnum.Nearest });
            var label=new Label { Text=name,TextOverrunBehavior=TextServer.OverrunBehavior.TrimEllipsis,HorizontalAlignment=HorizontalAlignment.Center,MouseFilter=MouseFilterEnum.Ignore };
            label.AddThemeFontSizeOverride("font_size",14);body.AddChild(label);
            var status=new Label { Text=pending>0?"施工中":S(tile["visibility"])=="memory"?"历史记录":"已落成",HorizontalAlignment=HorizontalAlignment.Center,MouseFilter=MouseFilterEnum.Ignore,Modulate=new Color("b8c6a6") };
            status.AddThemeFontSizeOverride("font_size",14);body.AddChild(status);
        }
    }
    private void RenderHumanCapacities(JsonObject tile)
    {
        HumanText("地块资源容量",color:"a9bbaa");
        var row=new HFlowContainer();row.AddThemeConstantOverride("h_separation",10);_humanContent.AddChild(row);
        foreach(var r in tile["resources"]!.AsArray().Where(r=>F(r![1])>0))
        {
            string key=S(r![0]);var box=new HBoxContainer();row.AddChild(box);
            box.AddChild(new TextureRect { Texture=RtsArt.Resource(key),CustomMinimumSize=new Vector2(22,22),ExpandMode=TextureRect.ExpandModeEnum.IgnoreSize,StretchMode=TextureRect.StretchModeEnum.KeepAspectCentered });
            box.AddChild(new Label { Text=HumanLabel(key)+" "+Quantity(r[1]) });
        }
    }
}
