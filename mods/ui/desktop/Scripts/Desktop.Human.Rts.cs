using Godot;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using static WorldMap;

public partial class Desktop
{
    private JsonObject? _rtsData,_rtsHover;
    private bool _productionQueueMode=true;
    private int _rtsEpoch,_rtsCount=1;
    private double _rtsHoverTime;
    private bool _rtsExpanded;
    private bool _rtsCountPending;
    private int _rtsCountEdit;
    private int _rtsRestoreFocusEpoch=-1,_rtsRestoreCaret;
    private Label? _rtsPendingHint;
    private PanelContainer? _rtsTooltip;
    private VBoxContainer? _rtsTooltipBody;
    private readonly Dictionary<string,Button> _rtsCards=new();
    private readonly Dictionary<Button,bool> _rtsAvailability=new();
    private Vector2 _rtsHoverAnchor;
    private static bool Yes(JsonNode? node)=>node?.GetValue<bool>()==true;

    private void ResetRts()
    {
        _rtsEpoch++;_rtsData=null;_rtsCountPending=false;_rtsPendingHint=null;_rtsCards.Clear();_rtsAvailability.Clear();HideRtsTooltip();
    }
    private bool EnsureRtsCell()
    {
        var sites=HumanFacts["building_sites"]!["sites"]!.AsArray();
        if(sites.Count==0)return false;
        if(!sites.Any(s=>I(s!["position"]![0])==_humanCell.X&&I(s["position"]![1])==_humanCell.Y))
            _humanCell=new(I(sites[0]!["position"]![0]),I(sites[0]!["position"]![1]));
        return true;
    }
    private void RenderRtsCatalog()
    {
        if(!EnsureRtsCell()){HumanText("没有可操作的本国地块。",true);return;}
        var point=_humanCell;int epoch=_rtsEpoch;string page=_humanPage;
        HumanText($"({point.X}, {point.Y}) · 正在读取本格条件…",true);
        HumanQuery("site_options",new JsonObject { ["x"]=point.X,["y"]=point.Y,["count"]=_rtsCount,["queued"]=_productionQueueMode },row=>
        {
            if(epoch!=_rtsEpoch||page!=_humanPage||point!=_humanCell)return;
            _rtsData=row["data"]!.AsObject();ClearHumanChildren(_humanContent);FillRtsCatalog(_rtsData,page=="recruit");
            if(epoch==_rtsRestoreFocusEpoch&&_humanContent.FindChild("RtsRecruitCount",true,false) is SpinBox replacement)
            {
                _rtsRestoreFocusEpoch=-1;
                Callable.From(()=>{if(IsInstanceValid(replacement)){replacement.GetLineEdit().GrabFocus();replacement.GetLineEdit().Deselect();replacement.GetLineEdit().CaretColumn=_rtsRestoreCaret;}}).CallDeferred();
            }
        });
    }
    private void RtsChangeCell(Vector2I at)
    {
        SetHumanSelectionCell(at);
        _humanCell=at;HumanMessage("已切换地块，以下为当前地块的条件。");
        _map.SelectAndFocus(at);RenderHumanPage();
    }
    private void FillRtsCatalog(JsonObject data,bool recruit)
    {
        var header=new HBoxContainer();_humanContent.AddChild(header);
        var title=new Label { Text=$"{data["name"]}  ({_humanCell.X}, {_humanCell.Y})",SizeFlagsHorizontal=SizeFlags.ExpandFill };
        title.AddThemeFontSizeOverride("font_size",20);header.AddChild(title);
        var locate=Button(header,"定位",()=>_map.SelectAndFocus(_humanCell));HumanArt.Style(locate,"pin");
        var sites=HumanFacts["building_sites"]!["sites"]!.AsArray();
        if(sites.Count>1)
        {
            int index=Array.FindIndex(sites.ToArray(),s=>I(s!["position"]![0])==_humanCell.X&&I(s["position"]![1])==_humanCell.Y);
            foreach(var (step,text) in new[]{(-1,"←"),(1,"→")})
            {
                int target=(index+step+sites.Count)%sites.Count;var p=sites[target]!["position"]!;var at=new Vector2I(I(p[0]),I(p[1]));
                var button=Button(header,text,()=>RtsChangeCell(at));button.TooltipText=$"{(step<0?"上一":"下一")}本国地块 ({at.X}, {at.Y})";HumanArt.Style(button);
            }
        }
        var tabs=new HBoxContainer();_humanContent.AddChild(tabs);
        foreach(var (page,text,icon) in new[]{("build","建造","build"),("recruit","征兵","army"),("demolish","管理","disband"),("tile","地块","map")})
        {string selected=page;var b=Button(tabs,text,()=>OpenHumanPage(selected));b.SizeFlagsHorizontal=SizeFlags.ExpandFill;HumanArt.Style(b,icon);b.Disabled=page==_humanPage;}
        if(data["terrain"] is JsonObject terrain)
        {
            var ground=HumanText($"地形：{terrain["label"]} · 地形惩罚 +{data["penalty"]}% 金币",color:"dec58f");
            ground.Name="RtsTerrain";ground.AddThemeFontSizeOverride("font_size",15);
            ground.TooltipText="本格资源容量与建筑要求见各图标悬浮说明；地形、工程院和国策按正式报价分步取整。";
        }
        var queueMode=new CheckButton { Name="ProductionQueueMode",Text="生产队列 · 预付材料，按期执行",ButtonPressed=_productionQueueMode };
        queueMode.AddThemeFontSizeOverride("font_size",14);_humanContent.AddChild(queueMode);
        queueMode.Toggled+=value=>{_productionQueueMode=value;RenderHumanPage();};
        bool ordered=Yes(data["ordered"]);var construction=data["construction"]!.AsArray();
        string pending=string.Join("、",construction.Select(b=>HumanLabel(S(b!["kind"]))));
        HumanText((construction.Count>0?$"施工中：{pending} · ":"尚未施工 · ")+
            (ordered?"本期剩余 0/1":"本期可建 1/1")+$" · 建筑位 {data["occupied"]}/{data["capacity"]}",color:ordered?"dfbc75":"a7d5aa");
        if(!recruit)foreach(var item in data["demolitions"]!.AsArray().Where(b=>!Yes(b!["completed"])))
        {
            var row=item!.AsObject();string name=HumanLabel(S(row["kind"]));
            var cancel=HumanButton("撤销 "+name+" · 返还50%",()=>ConfirmDemolition(row),"disband");
            cancel.TooltipText=Yes(row["available"])?"退款："+Resources(row["refunds"]):S(row["reason"]);
            _rtsAvailability[cancel]=Yes(row["available"]);
        }
        RenderProductionOrders(data[recruit?"recruitment_orders":"orders"] as JsonArray??new JsonArray(),recruit,compact:true);
        HumanButton("全国生产队列",()=>OpenHumanPage("production"),"build");
        if(recruit)
        {
            var amount=new HBoxContainer();_humanContent.AddChild(amount);amount.AddChild(new Label { Text="每次征召" });
            var count=new SpinBox { Name="RtsRecruitCount",UpdateOnTextChanged=true,SelectAllOnFocus=true,MinValue=1,MaxValue=20,Step=1,Value=_rtsCount,CustomMinimumSize=new Vector2(100,0) };amount.AddChild(count);
            count.ValueChanged+=async value=>
            {
                _rtsCount=(int)value;_rtsCountPending=true;RefreshRtsButtons();
                int edit=++_rtsCountEdit,epoch=_rtsEpoch;var cell=_humanCell;
                await ToSignal(GetTree().CreateTimer(.35),SceneTreeTimer.SignalName.Timeout);
                if(edit!=_rtsCountEdit||epoch!=_rtsEpoch||_humanPage!="recruit"||cell!=_humanCell)return;
                bool editing=IsInstanceValid(count)&&count.GetLineEdit().HasFocus();
                int caret=editing?count.GetLineEdit().CaretColumn:0;
                RenderHumanPage();
                if(editing){_rtsRestoreFocusEpoch=_rtsEpoch;_rtsRestoreCaret=caret;}
            };
            if(!data["units"]!.AsArray().Any(u=>Yes(u!["has_facility"])))HumanText("本格缺少已落成的兵营／军屯。先建造所需设施。",color:"e0b49b");
        }
        var hint=HumanText(_productionQueueMode?"点击入队并预付 · 未开始全退，施工中50%，完工0%":recruit?"可用优先 · 悬停看费用，停留看属性与维持消耗":"每格每回合施工一次 · 悬停看价格，停留看收益",color:"a3b7ad");
        hint.AddThemeFontSizeOverride("font_size",14);
        _rtsPendingHint=HumanText("本格已有指令正在执行或待执行，不重复排队。",color:"e1c48b");_rtsPendingHint.AddThemeFontSizeOverride("font_size",14);
        var grid=new GridContainer { Name="RtsCatalog",Columns=3,SizeFlagsHorizontal=SizeFlags.ExpandFill };
        grid.AddThemeConstantOverride("h_separation",8);grid.AddThemeConstantOverride("v_separation",8);_humanContent.AddChild(grid);
        foreach(var item in data[recruit?"units":"buildings"]!.AsArray())
        {
            var row=item!.AsObject();string kind=S(row["kind"]);bool available=Yes(row["available"]);
            var card=new Button { Name="Rts_"+kind.Split(':')[^1],CustomMinimumSize=new Vector2(126,132),SizeFlagsHorizontal=SizeFlags.ExpandFill,FocusMode=FocusModeEnum.All,TextureFilter=TextureFilterEnum.Nearest };
            HumanArt.Style(card);grid.AddChild(card);_rtsCards[kind]=card;_rtsAvailability[card]=available;
            var margin=new MarginContainer { MouseFilter=MouseFilterEnum.Ignore };
            margin.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);margin.OffsetLeft=7;margin.OffsetTop=6;margin.OffsetRight=-7;margin.OffsetBottom=-6;card.AddChild(margin);
            var body=new VBoxContainer { MouseFilter=MouseFilterEnum.Ignore };body.AddThemeConstantOverride("separation",3);margin.AddChild(body);
            var icon=new TextureRect { Texture=recruit?RtsArt.Unit(kind,_map.NationColor(HumanActor)):RtsArt.Building(kind),
                ExpandMode=TextureRect.ExpandModeEnum.IgnoreSize,StretchMode=TextureRect.StretchModeEnum.KeepAspectCentered,
                CustomMinimumSize=new Vector2(0,52),MouseFilter=MouseFilterEnum.Ignore,Modulate=new Color(1,1,1,available?1:.38f) };body.AddChild(icon);
            string caption=S(row["label"])+(Yes(row["leveled"])?row["level"]==null?" · 满级":$" · {row["level"]}级":"");
            var name=new Label { Text=caption,HorizontalAlignment=HorizontalAlignment.Center,MouseFilter=MouseFilterEnum.Ignore,
                TextOverrunBehavior=TextServer.OverrunBehavior.TrimEllipsis,Modulate=new Color(available?"e8dfbc":"8a938b") };
            name.AddThemeFontSizeOverride("font_size",16);body.AddChild(name);
            RtsCosts(body,row["costs"],compact:true,dim:!available);
            var status=new Label { Text=available?(_productionQueueMode?"加入队列":recruit?$"可征 {row["quota"]} 支":"可建造"):S(row["reasons"]?.AsArray().FirstOrDefault()),
                TextOverrunBehavior=TextServer.OverrunBehavior.TrimEllipsis,HorizontalAlignment=HorizontalAlignment.Center,
                MouseFilter=MouseFilterEnum.Ignore,Modulate=new Color(available?"9bca9a":"a19a86") };
            status.AddThemeFontSizeOverride("font_size",14);body.AddChild(status);
            card.MouseEntered+=()=>ShowRtsTooltip(row,card.GetGlobalRect().Position);
            card.MouseExited+=HideRtsTooltip;card.FocusEntered+=()=>ShowRtsTooltip(row,card.GetGlobalRect().Position);card.FocusExited+=HideRtsTooltip;
            card.Pressed+=()=>
            {
                if(!available||!HumanCanQueue||_rtsCountPending)return;
                _lastRtsKinds[recruit?"recruit":"build"]=kind;
                HideRtsTooltip();_humanOperation=_productionQueueMode?(recruit?"queue_recruit":"queue_build"):(recruit?"recruit":"build");
                var args=new JsonObject { ["kind"]=kind,["x"]=_humanCell.X,["y"]=_humanCell.Y };
                if(recruit)args["count"]=_rtsCount;
                SubmitHuman(_humanOperation,args);
            };
        }
        RefreshRtsButtons();
        var previous=_returnRtsFocus is string focus?_rtsCards.GetValueOrDefault(focus):null;
        int? savedScroll=_restoreRtsScroll;_restoreRtsScroll=null;_returnRtsFocus=null;
        if(previous!=null||savedScroll!=null)_=RestoreRtsPosition(previous,savedScroll,_rtsEpoch);
    }
    private async System.Threading.Tasks.Task RestoreRtsPosition(Button? button,int? scroll,int epoch)
    {
        // The quote arrives before VBox/ScrollContainer have their new extent.
        // A deferred call in that same frame still clamps scrolling to zero.
        await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
        await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
        if(epoch!=_rtsEpoch||!_humanDrawer.IsInsideTree())return;
        if(scroll is int offset)_humanScroll.ScrollVertical=offset;
        if(button!=null&&IsInstanceValid(button)&&button.IsInsideTree())
        {_humanScroll.EnsureControlVisible(button);button.GrabFocus();}
    }
    private void RefreshRtsButtons()
    {
        bool catalog=_humanPage is "build" or "recruit";
        bool pending=catalog&&!_productionQueueMode&&PendingHumanSite(_humanPage=="build"?"build":"recruit",_humanCell);
        if(_rtsPendingHint!=null&&IsInstanceValid(_rtsPendingHint))_rtsPendingHint.Visible=pending;
        foreach(var (button,available) in _rtsAvailability)if(IsInstanceValid(button))
        {
            bool card=catalog&&_rtsCards.Values.Contains(button);
            button.Disabled=!available||!(card?HumanCanQueue:HumanCanWrite)||card&&(_rtsCountPending||pending);
        }
    }
    private void RtsCosts(BoxContainer parent,JsonNode? costs,bool compact=false,bool dim=false)
    {
        if(costs==null){parent.AddChild(new Label { Text="无下一档报价",MouseFilter=MouseFilterEnum.Ignore });return;}
        var line=new HBoxContainer { Alignment=BoxContainer.AlignmentMode.Center,MouseFilter=MouseFilterEnum.Ignore };
        line.AddThemeConstantOverride("separation",4);parent.AddChild(line);
        foreach(var (key,amount) in costs.AsObject())
        {
            line.AddChild(new TextureRect { Texture=RtsArt.Resource(key),CustomMinimumSize=new Vector2(18,18),ExpandMode=TextureRect.ExpandModeEnum.IgnoreSize,
                StretchMode=TextureRect.StretchModeEnum.KeepAspectCentered,MouseFilter=MouseFilterEnum.Ignore,Modulate=new Color(1,1,1,dim ? .45f : 1) });
            var text=new Label { Text=(compact?"":HumanLabel(key)+" ")+Quantity(amount),MouseFilter=MouseFilterEnum.Ignore,Modulate=new Color(dim?"919588":"ead99f") };
            text.AddThemeFontSizeOverride("font_size",compact?14:16);line.AddChild(text);
        }
        if(costs.AsObject().Count==0)line.AddChild(new Label { Text="免费",MouseFilter=MouseFilterEnum.Ignore });
    }
    private void HideRtsTooltip(){_rtsHover=null;_rtsTooltip?.Hide();}
    private void ShowRtsTooltip(JsonObject row,Vector2 anchor)
    {
        _rtsHover=row;_rtsHoverAnchor=anchor;_rtsHoverTime=0;_rtsExpanded=false;FillRtsTooltip();
    }
    private void TickRts(double delta)
    {
        if(_rtsHover==null||_pauseMenu.Visible){if(_pauseMenu.Visible)HideRtsTooltip();return;}
        _rtsHoverTime+=delta;
        if(!_rtsExpanded&&_rtsHoverTime>=.85){_rtsExpanded=true;FillRtsTooltip();}
    }
    private void FillRtsTooltip()
    {
        if(_rtsHover==null)return;var row=_rtsHover;
        if(_rtsTooltip==null)
        {
            _rtsTooltip=new PanelContainer { ZIndex=70,MouseFilter=MouseFilterEnum.Ignore,TextureFilter=TextureFilterEnum.Nearest };
            _rtsTooltip.AddThemeStyleboxOverride("panel",HumanArt.Box("panel"));AddChild(_rtsTooltip);
            _rtsTooltipBody=new VBoxContainer { MouseFilter=MouseFilterEnum.Ignore };_rtsTooltipBody.AddThemeConstantOverride("separation",7);_rtsTooltip.AddChild(_rtsTooltipBody);
        }
        ClearHumanChildren(_rtsTooltipBody!);
        void Text(string text,string color="d7e0cc",int font=16)
        {
            var label=new Label { Text=text,CustomMinimumSize=new Vector2(360,0),AutowrapMode=TextServer.AutowrapMode.WordSmart,MouseFilter=MouseFilterEnum.Ignore };
            label.AddThemeColorOverride("font_color",new Color(color));label.AddThemeFontSizeOverride("font_size",font);_rtsTooltipBody!.AddChild(label);
        }
        Text(S(row["label"])+(Yes(row["leveled"])&&row["level"]!=null?$" · {row["level"]}级":""),"e6cb80",20);
        Text("本格实际价格");RtsCosts(_rtsTooltipBody!,row["costs"]);
        if(row["price"] is JsonObject price)
        {
            Text("基础材料："+Resources(row["base_costs"]),"adbbac");
            if(row["cost_breakdown"] is JsonArray steps)foreach(var step in steps)
            {
                string id=S(step!["id"]);if(id=="base"){Text($"基础金币 {step["after"]}","c7ba97",14);continue;}
                if(id!="terrain"&&I(step["percent"])==0)continue;
                string name=id switch { "terrain"=>"地形惩罚","discount"=>"工程院折扣",_=>"国策调整" };
                Text($"{name} {I(step["percent"]):+0;-0;0}%（{I(step["change"]):+0;-0;0} 金）→ {step["after"]} 金","c7ba97",14);
            }
            Text("木材不参与金币加成；各步取整已计入实付。","aaba9f",14);
        }
        foreach(var condition in row["requirements"]!.AsArray())
            Text((Yes(condition!["met"])?"✓ ":"× ")+S(condition["text"]),Yes(condition["met"])?"a2cb9f":"edaa90",14);
        if(_rtsExpanded)
        {
            Text(S(row["description"]));
            if(row["expected_net"]!=null)
            {
                double net=F(row["expected_net"]);
                Text($"预计净收益 {net:+0.##;-0.##;0} 金／周期"+(row["payback"]!=null?$" · 回本约 {F(row["payback"]):0.#} 期":" · 无正向回本期"),net>0?"b9d6a1":"d0ba99");
            }
            if(row["estimate_note"]!=null)Text(S(row["estimate_note"]),"9cae9f",14);
            foreach(var note in (row["assumptions"] as JsonArray??new JsonArray()).Take(2))Text(S(note),"aebaa4",14);
            if(row["warning"]!=null)Text(S(row["warning"]),"e5b77a",14);
        }
        else Text("稍作停留，展开作用与收益说明…","94a99a",14);
        _rtsTooltip.Visible=true;
        void Position()
        {
            if(_rtsTooltip==null||!_rtsTooltip.Visible)return;
            _rtsTooltip.Size=new Vector2(388,0);
            _rtsTooltip.Position=new Vector2(Math.Clamp(_rtsHoverAnchor.X-400,12,Math.Max(12,Size.X-404)),
                Math.Clamp(_rtsHoverAnchor.Y,66,Math.Max(66,Size.Y-76-_rtsTooltip.Size.Y)));
        }
        Position();Callable.From(Position).CallDeferred();
    }
}
