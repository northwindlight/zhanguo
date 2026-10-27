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
        HumanText("地块建筑",true).Name="TileBuildingHeading";
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
            if(S(tile["visibility"])=="live"&&S(tile["owner"])==HumanActor&&HasHumanOperation("demolish"))
            {card.MouseDefaultCursorShape=CursorShape.PointingHand;card.TooltipText+="\n点击管理：撤销施工／拆除建筑";card.Pressed+=()=>OpenHumanPage("demolish");}
            var body=new VBoxContainer { MouseFilter=MouseFilterEnum.Ignore };body.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);body.OffsetLeft=5;body.OffsetTop=5;body.OffsetRight=-5;body.OffsetBottom=-5;card.AddChild(body);
            body.AddChild(new TextureRect { Texture=RtsArt.Building(kind),CustomMinimumSize=new Vector2(0,62),ExpandMode=TextureRect.ExpandModeEnum.IgnoreSize,
                StretchMode=TextureRect.StretchModeEnum.KeepAspectCentered,MouseFilter=MouseFilterEnum.Ignore,TextureFilter=TextureFilterEnum.Nearest });
            var label=new Label { Text=name,TextOverrunBehavior=TextServer.OverrunBehavior.TrimEllipsis,HorizontalAlignment=HorizontalAlignment.Center,MouseFilter=MouseFilterEnum.Ignore };
            label.AddThemeFontSizeOverride("font_size",14);body.AddChild(label);
            var status=new Label { Text=pending>0?"施工中":S(tile["visibility"])=="memory"?"历史记录":"已落成",HorizontalAlignment=HorizontalAlignment.Center,MouseFilter=MouseFilterEnum.Ignore,Modulate=new Color("b8c6a6") };
            status.AddThemeFontSizeOverride("font_size",14);body.AddChild(status);
        }
    }
    private void RenderHumanDemolition()
    {
        if(!EnsureRtsCell()){HumanText("没有可管理的本国建筑。",true);return;}
        int epoch=_rtsEpoch;var cell=_humanCell;
        HumanText($"({cell.X}, {cell.Y}) · 建筑管理",true);
        HumanText("正在核对施工与返还…",color:"a7b8a9");
        HumanQuery("site_options",new JsonObject { ["x"]=cell.X,["y"]=cell.Y },response=>
        {
            if(epoch!=_rtsEpoch||_humanPage!="demolish"||cell!=_humanCell)return;
            var data=response["data"]!;ClearHumanChildren(_humanContent);
            HumanText($"{data["name"]} ({cell.X}, {cell.Y}) · 建筑管理",true);
            HumanButton("← 返回建造",()=>OpenHumanPage("build"),"build");
            HumanText("施工中返还实际材料的50%；已落成拆除零返还。撤销不恢复本格本回合的施工额度。",color:"d9c794");
            var rows=data["demolitions"]!.AsArray();
            if(rows.Count==0)HumanText("这里没有建筑。",color:"a7b8a9");
            foreach(var item in rows)
            {
                var row=item!.AsObject();bool complete=Yes(row["completed"]),available=Yes(row["available"]);
                var panel=new PanelContainer();panel.AddThemeStyleboxOverride("panel",HumanArt.Box("panel"));_humanContent.AddChild(panel);
                var body=new HBoxContainer();panel.AddChild(body);
                body.AddChild(new TextureRect { Texture=RtsArt.Building(S(row["kind"])),CustomMinimumSize=new Vector2(58,72),ExpandMode=TextureRect.ExpandModeEnum.IgnoreSize,StretchMode=TextureRect.StretchModeEnum.KeepAspectCentered });
                var info=new VBoxContainer { SizeFlagsHorizontal=SizeFlags.ExpandFill };body.AddChild(info);
                var label=new Label { Text=HumanLabel(S(row["kind"]))+(S(row["kind"])=="native:castle"?$" · {row["level"]}级":"")+$" #{row["id"]}",TextOverrunBehavior=TextServer.OverrunBehavior.TrimEllipsis };label.AddThemeFontSizeOverride("font_size",16);info.AddChild(label);
                info.AddChild(new Label { Text=complete?"已落成 · 无返还":"施工中 · 返还50%",Modulate=new Color(complete?"a8b4a0":"d8c68b") });
                if(!complete)RtsCosts(info,row["refunds"],compact:true);
                var button=new Button { Name="Demolish_"+I(row["id"]),Text=complete?"拆除":"撤销",TooltipText=available?(complete?"永久拆除，不返还材料":"退回实际材料的一半"):S(row["reason"]) };
                HumanArt.Style(button,"disband");body.AddChild(button);button.Pressed+=()=>{if(available)ConfirmDemolition(row);};
                _rtsAvailability[button]=available;
            }
            RefreshHumanButtons();
        });
    }
    private void ConfirmDemolition(JsonObject row)
    {
        if(!HumanCanWrite)return;
        bool complete=Yes(row["completed"]);string name=HumanLabel(S(row["kind"]));
        string warning=complete?"已落成建筑拆除后不返还材料。":"撤销后返还实际材料的50%："+Resources(row["refunds"])+"。\n本格本回合施工额度仍已用尽。";
        if(S(row["kind"])=="native:town-hall")warning+="\n注意：失去最后一座市政厅会在结算时导致亡国。";
        ConfirmHuman(complete?"拆除 "+name:"撤销 "+name+" 的施工",warning,
            ()=>SubmitHuman("demolish",new JsonObject { ["id"]=I(row["id"]) }));
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
