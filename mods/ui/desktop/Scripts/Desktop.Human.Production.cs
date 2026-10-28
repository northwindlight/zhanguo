using Godot;
using System;
using System.Linq;
using System.Text.Json.Nodes;
using static WorldMap;

public partial class Desktop
{
    private void RenderProductionQueue()
    {
        HumanText("全国生产",true);
        HumanText("入队即预付；每格每期一次建设，征兵按设施额度分批。等待不会重复扣款。",color:"b6c6aa");
        var prepaid=HumanFacts["econ"]?["prepaid_stocks"] as JsonArray??new JsonArray();
        HumanText("尚未消耗的预付款："+string.Join(" · ",prepaid.Select(r=>HumanLabel(S(r![0]))+" "+Quantity(r[1]))),color:"e1ca8e");
        HumanText("建设",true);
        RenderProductionOrders(_humanState?["production_queue"]?["construction"] as JsonArray??new JsonArray(),false);
        HumanText("征兵",true);
        RenderProductionOrders(_humanState?["production_queue"]?["recruitment"] as JsonArray??new JsonArray(),true);
        HumanButton("返回建设",()=>OpenHumanPage("build"),"build");
    }

    private void RenderProductionOrders(JsonArray orders,bool recruit,bool compact=false)
    {
        var rows=orders.Where(r=>!compact||recruit||S(r!["status"])!="construction").ToArray();
        if(rows.Length==0){if(!compact)HumanText("暂无条目",color:"8fa591");return;}
        if(compact)
        {
            var summary=new HBoxContainer();summary.AddThemeConstantOverride("separation",8);_humanContent.AddChild(summary);
            var count=new Label { Text=recruit?$"待征 {rows.Sum(r=>I(r!["remaining"]))} 支":"待建 "+rows.Length+" 项",Modulate=new Color("d8c58c") };
            count.AddThemeFontSizeOverride("font_size",14);summary.AddChild(count);
            foreach(var node in rows.Take(3))summary.AddChild(new TextureRect {
                Texture=recruit?RtsArt.Unit(S(node!["kind"]),_map.NationColor(HumanActor)):RtsArt.Building(S(node!["kind"])),
                CustomMinimumSize=new Vector2(28,28),ExpandMode=TextureRect.ExpandModeEnum.IgnoreSize,StretchMode=TextureRect.StretchModeEnum.KeepAspectCentered,
                TooltipText=HumanLabel(S(node["kind"]))+" · "+S(node["reason"])+"\n取消返还："+Resources(node["refunds"]) });
            if(rows.Length>3)summary.AddChild(new Label { Text="… +"+(rows.Length-3) });
            return;
        }
        foreach(var node in rows)
        {
            var row=node!.AsObject();int id=I(row["id"]);bool started=S(row["status"])=="construction";
            var at=new Vector2I(I(row["position"]![0]),I(row["position"]![1]));
            var panel=new PanelContainer { Name=$"ProductionOrder_{(recruit?"Recruit":"Build")}_{id}" };
            panel.AddThemeStyleboxOverride("panel",HumanArt.Box("panel"));_humanContent.AddChild(panel);
            var body=new HBoxContainer();panel.AddChild(body);
            body.AddChild(new TextureRect { Texture=recruit?RtsArt.Unit(S(row["kind"]),_map.NationColor(HumanActor)):RtsArt.Building(S(row["kind"])),
                CustomMinimumSize=new Vector2(42,48),ExpandMode=TextureRect.ExpandModeEnum.IgnoreSize,StretchMode=TextureRect.StretchModeEnum.KeepAspectCentered });
            var info=new VBoxContainer { SizeFlagsHorizontal=SizeFlags.ExpandFill };body.AddChild(info);
            string title=HumanLabel(S(row["kind"]))+(recruit?$" · {row["completed"]}/{row["count"]}":S(row["kind"])=="native:castle"?$" · {row["level"]}级":"");
            var label=new Label { Text=title+$"  ({at.X},{at.Y})",TextOverrunBehavior=TextServer.OverrunBehavior.TrimEllipsis };
            label.AddThemeFontSizeOverride("font_size",15);info.AddChild(label);
            var reason=new Label { Text=started?"施工中":recruit?$"待征 {row["remaining"]} 支 · "+S(row["reason"]):$"队列第 {row["line_position"]} 项 · "+S(row["reason"]),
                AutowrapMode=TextServer.AutowrapMode.WordSmart,Modulate=new Color("a9bba0") };
            reason.AddThemeFontSizeOverride("font_size",13);info.AddChild(reason);
            var buttons=new VBoxContainer();body.AddChild(buttons);
            var locate=Button(buttons,"定位",()=>_map.SelectAndFocus(at));HumanArt.Style(locate,"pin");
            var cancel=Button(buttons,started?"拆除50%":"取消全退",()=>
            {
                if(!HumanCanWrite)return;
                ConfirmHuman(started?"拆除在建建筑":"取消未完成生产",(recruit?$"已生成 {row["completed"]} 支不退款；剩余 {row["remaining"]} 支退回：":"实际返还：")+Resources(row["refunds"]),
                    ()=>SubmitHuman(recruit?"cancel_recruit_order":started?"demolish":"cancel_build_order",new JsonObject { ["id"]=id }));
            });
            HumanArt.Style(cancel,"disband");cancel.Name=$"CancelProduction_{(recruit?"Recruit":"Build")}_{id}";
            cancel.TooltipText=(S(row["cancel_reason"]) is {Length:>0} blocked?blocked+"\n":"")+"返还："+Resources(row["refunds"]);
            _rtsAvailability[cancel]=recruit||Yes(row["can_cancel"]);
        }
        RefreshRtsButtons();
    }
}
