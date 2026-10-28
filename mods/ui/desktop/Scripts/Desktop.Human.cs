using Godot;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using static WorldMap;

public partial class Desktop
{
    private JsonObject? _humanState,_humanTurn;
    private bool HumanMode=>_humanState!=null;
    private string HumanActor=>S(_humanState?["actor"]);
    private bool _humanBusy,_humanPaused,_humanStarted,_humanEnded,_humanNeedsFrame,_humanResultReady;
    private string _humanSubmittedOperation="";
    private Vector2I? _humanSubmittedCell;
    private int _humanSubmittedPeriod;
    private string _humanOperation="",_humanPage="",_humanWrite="",_humanTargetMode="";
    private Control? _observerDock,_mapHint;
    private HBoxContainer _humanDock=null!;
    private MapDrawer _humanDrawer=null!;
    private VBoxContainer _humanContent=null!;
    private ScrollContainer _humanScroll=null!;
    private Label _humanReceipt=null!,_targetSummary=null!;
    private readonly Dictionary<string,Button> _humanNav=new();
    private readonly List<Button> _humanWrites=new();
    private readonly HashSet<int> _selectedArmies=new();
    private readonly Dictionary<string,Action<JsonObject>> _humanQueries=new();
    private readonly List<string> _humanHistory=new();
    private readonly Dictionary<string,Func<JsonNode?>> _humanFields=new();
    private JsonObject _humanExtra=new();
    private Button? _humanSubmit;
    private ConfirmationDialog _humanConfirm=null!;
    private Vector2I _humanCell=new(-1,-1);
    private int _savedHumanPeriod=-1;
    private bool HumanCanWrite=>HumanMode&&_humanTurn!=null&&!_humanNeedsFrame&&!_humanEnded&&!_humanBusy&&!_humanPaused&&
        !HasTimeline&&!_pauseMenu.Visible&&!_closing&&_humanState?["can_act"]?.GetValue<bool>()==true;
    private JsonObject HumanFacts=>_humanState!["facts"]!.AsObject();
    private JsonArray HumanOperations=>_humanState?["operations"]?.AsArray()??new JsonArray();
    private string HumanLabel(string id)=>S(_map.Frame?["labels"]?[id]) is {Length:>0} name?name:id;
    private string NationLabel(string id)
    {
        string key=id.StartsWith("nation:")?"scenario:"+id[7..]:id;
        if(S(_map.Frame?["names"]?[key]) is {Length:>0} name)return name;
        return S(_humanState?["facts"]?["foreign"]?["diplomacy"]?["alliances"]?.AsArray().FirstOrDefault(b=>S(b!["id"])==id)?["name"]) is {Length:>0} bloc?bloc:id;
    }
    private bool HasHumanOperation(string name)=>HumanOperations.Any(o=>S(o!["operation"])==name);
    private static void ClearHumanChildren(Node node)
    {foreach(Node child in node.GetChildren()){node.RemoveChild(child);child.QueueFree();}}

    private void BuildHumanControls()
    {
        _humanDock=new HBoxContainer { Visible=false,ZIndex=12,MouseFilter=MouseFilterEnum.Ignore };
        _humanDock.SetAnchorsAndOffsetsPreset(LayoutPreset.BottomWide);
        _humanDock.OffsetLeft=16;_humanDock.OffsetRight=-16;_humanDock.OffsetTop=-60;_humanDock.OffsetBottom=-12;
        _humanDock.AddThemeConstantOverride("separation",5);AddChild(_humanDock);
        foreach(var (key,title,icon) in new[]{("build","建设","build"),("army","军队","army"),("market","市场","market"),
            ("diplomacy","外交","diplomacy"),("alliance","联盟","alliance"),("intel","情报","intelligence"),("nation","国家","nation"),("end","结束回合","end-turn")})
        {
            string page=key;
            var button=Button(_humanDock,title,()=>{if(page=="end")HumanEndTurn();else OpenHumanPage(page);});
            button.SizeFlagsHorizontal=SizeFlags.ExpandFill;button.CustomMinimumSize=new Vector2(0,44);
            HumanArt.Style(button,icon);button.AddThemeFontSizeOverride("font_size",16);_humanNav[key]=button;
        }
        _humanDrawer=Drawer("本国政务",MapDrawer.Side.Right);
        _humanDrawer.AddThemeStyleboxOverride("panel",HumanArt.Box("panel"));
        _targetSummary=new Label { Visible=false,MaxLinesVisible=4,TextOverrunBehavior=TextServer.OverrunBehavior.TrimEllipsis,AutowrapMode=TextServer.AutowrapMode.WordSmart,Modulate=new Color("a9cddd") };
        _humanDrawer.Body.AddChild(_targetSummary);
        var scroll=new ScrollContainer { SizeFlagsVertical=SizeFlags.ExpandFill,HorizontalScrollMode=ScrollContainer.ScrollMode.Disabled };
        _humanScroll=scroll;
        _humanDrawer.Body.AddChild(scroll);
        _humanContent=new VBoxContainer { SizeFlagsHorizontal=SizeFlags.ExpandFill };
        _humanContent.AddThemeConstantOverride("separation",10);scroll.AddChild(_humanContent);
        _marketFooter=new HBoxContainer { Visible=false };_marketFooter.AddThemeConstantOverride("separation",6);_humanDrawer.Body.AddChild(_marketFooter);
        BuildHumanQueue();
        _humanReceipt=new Label { Text="点地块查看驻军与建设；操作后在这里显示实际回执。",AutowrapMode=TextServer.AutowrapMode.WordSmart,
            MaxLinesVisible=5,TextOverrunBehavior=TextServer.OverrunBehavior.TrimEllipsis,Modulate=new Color("c6d4b4") };
        _humanDrawer.Body.AddChild(_humanReceipt);
        _humanConfirm=new ConfirmationDialog { Title="确认操作",DialogText="",MinSize=new Vector2I(460,180),OkButtonText="确认",CancelButtonText="返回",Theme=Theme,DialogAutowrap=true };
        AddChild(_humanConfirm);_humanConfirm.Confirmed+=RunHumanConfirmation;
        _humanConfirm.AddThemeStyleboxOverride("panel",HumanArt.Box("panel"));
        HumanArt.Style(_humanConfirm.GetOkButton(),"check");HumanArt.Style(_humanConfirm.GetCancelButton());
        BuildHumanOutliner();
        _humanDrawer.CloseButton.Pressed+=()=>LayoutHuman();
        BuildHumanExit();
        _map.HumanTargetHovered=target=>
        {
            if(_humanTargetMode=="")return;
            if(target!=null)_lastArmyTarget=target.DeepClone().AsObject();
            _targetSummary.Text=target==null?"选择地图上的目标圈；Esc 取消军令。":
                $"本次可执行 {target["ids"]!.AsArray().Count} / 已选 {_selectedArmies.Count} 支"+
                (target["uncertain"]?.GetValue<bool>()==true?" · 迷雾内尝试，通路尚未确认":"")+
                " · 参与 "+string.Join("、",target["ids"]!.AsArray().Take(6).Select(i=>"#"+S(i)))+
                string.Concat((target["excluded"]?.AsArray()??new JsonArray()).Take(2).Select(e=>$"\n#{e!["aid"]}：{e["reason"]}"))+"\n完整名单与原因见「查看已选」。";
            _map.TooltipText=_targetSummary.Text;
        };
    }
    private void LayoutHuman()
    {
        if(_humanDrawer==null)return;
        float width=Math.Min(_humanPage=="market"?568:520,Size.X-328),height=Math.Max(180,Size.Y-300);
        float reserve=HumanMode&&Size.X>=1500&&!_outlinerCollapsed?296:0;
        _humanDrawer.SetBounds(_humanPage=="market"?new Rect2(Size.X-width-16-reserve,124,width,Size.Y-208):new Rect2(Size.X-width-16-reserve,216,width,height));
        LayoutHumanOutliner();
        _map.PlayerRightInset=_humanDrawer.IsOpen?width+32+reserve:_outlinerCollapsed?32:Size.X<1200?278:312;
        if(_humanConfirm!=null)_humanConfirm.MaxSize=new Vector2I((int)Math.Max(400,Size.X-48),(int)Math.Max(180,Size.Y-48));
    }
    private void SetHumanState(JsonObject state)
    {
        if(state["eliminated"]?.GetValue<bool>()==true&&state["last_known"] is JsonObject old)
        {
            _humanState=old;_humanEnded=true;_humanTurn=null;
            _humanReceipt.Text="最后一座市政厅已失守，本国亡国。画面保留最后合法观察。";
            return;
        }
        _humanState=state;
        _lastArmyTarget=null;
        if(_humanTurn!=null&&I(_humanTurn["period"])==I(state["period"]))_humanNeedsFrame=false;
        _desktopTitle.Text="战国 · "+NationLabel(HumanActor);
        _menuQuit.Text="退出游戏…";_menuContinue.Text="继续操作 · Esc";
        _humanDock.Visible=true;_observerDock?.Hide();_mapHint?.Hide();_bubbles.HideAll();_step.Visible=false;
        _humanNav["diplomacy"].Text="外交"+Badge(state["pending_end"]?["offers"]?.AsArray().Count??0);
        _humanNav["alliance"].Text="联盟"+Badge(state["pending_end"]?["votes"]?.AsArray().Count??0);
        _humanNav["intel"].Text="情报"+Badge(HumanFacts["intel"]?.AsArray().Count(m=>!_readIntel.Contains(I(m!["id"])))??0);
        var selected=OwnArmies.Where(u=>_selectedArmies.Contains(I(u["id"]))).ToArray();
        var places=selected.Select(u=>new Vector2I(I(u["x"]),I(u["y"]))).Distinct().ToArray();
        if(places.Length==1)_selectionCell=places[0]; // Follow this squad after its own move.
        int lost=_selectedArmies.Count-selected.Length;
        SetHumanSelectionCell(_selectionCell);
        RefreshArmySelection();_armyRangeDirty=true;
        if(lost>0)HumanMessage($"{lost} 支选中军队已阵亡或不再属于本国；其余选择保留。");
        if(!_humanStarted)
        {
            _humanStarted=true;_playbackPaused=false;Send("resume");
            _map.FocusNation(HumanActor);
        }
        if(_humanDrawer.IsOpen&&!_manualArmyTarget)RenderHumanPage();
        RenderHumanOutliner();
        RefreshHumanButtons();
    }
    private static string Badge(int count)=>count>0?$" ({count})":"";
    private IEnumerable<JsonNode> OwnArmies=>(_humanState?["facts"]?["army"]?.AsArray()??new JsonArray()).Where(u=>S(u!["owner"])==HumanActor&&F(u["hp"])>0).Select(u=>u!);
    private void RefreshHumanButtons()
    {
        if(_humanNav.Count==0||!HumanMode)return;
        _humanNav["end"].Disabled=!HumanCanWrite||_humanCommands.Count>0;RefreshRtsButtons();RefreshMarketButtons();
        foreach(var button in _humanWrites)if(IsInstanceValid(button))button.Disabled=!HumanCanWrite;
        if(_humanPage!="market"&&_humanSubmit!=null&&IsInstanceValid(_humanSubmit))_humanSubmit.Disabled=!HumanCanWrite||!HumanFormReady();
        if(!HasTimeline&&_humanTurn!=null&&!_humanEnded)
        {
            _bannerTitle.Text=$"{NationLabel(HumanActor)} · 第 {I(_humanState!["period"])+1} 回合 · {S(_humanState["nickname"])}";
            _bannerText.Text=_humanPaused?"已暂停 · 继续后可操作":_humanBusy?"正在提交操作…":"轮到你了 · 完成政务后点击底部「结束回合」";
        }
    }
    private void SendHumanRow(JsonObject row)
    {
        if(_host==null||_stopped||_closing)return;
        try{_host.StandardInput.WriteLine(row.ToJsonString());}
        catch(Exception e){Error(e.Message);}
    }
    private void HumanQuery(string query,JsonObject arguments,Action<JsonObject> callback)
    {
        string id="query/"+Guid.NewGuid().ToString("N");_humanQueries[id]=callback;
        SendHumanRow(new JsonObject { ["command"]="human_query",["actor"]=HumanActor,["request_id"]=id,["query"]=query,["arguments"]=arguments });
    }
    private bool ReceiveHuman(JsonObject row)
    {
        string type=S(row["type"]);
        if(type=="human_turn")
        {
            _humanCommands.Clear();RefreshHumanQueue();
            _humanTurn=(JsonObject)row.DeepClone();_humanEnded=false;_humanPaused=false;_humanBusy=false;
            _humanNeedsFrame=true;
            CancelHumanTarget();RefreshHumanButtons();return true;
        }
        if(type!="human_result")return false;
        string id=S(row["request_id"]);
        if(_humanQueries.Remove(id,out var callback))
        {
            if(row["ok"]?.GetValue<bool>()==true)callback(row);
            else HumanMessage(S(row["message"]),false);
            return true;
        }
        if(id!=_humanWrite)return true;
        bool processed=row["ok"]?.GetValue<bool>()==true;
        _humanResultReady=processed;
        var receipt=row["data"] as JsonObject;
        bool ok=processed&&receipt?["ok"]?.GetValue<bool>()==true;
        MarketReceived(_humanSubmittedOperation,ok);
        if(ok&&_humanSubmittedOperation is "buy" or "sell")RefreshLiveMarket();
        _humanReceipt.Modulate=new Color(ok?"c6e3ad":"ffb6a4");
        string result=processed?FormatHumanReceipt(receipt!):S(row["message"]);
        bool changedSite=_humanSubmittedCell is Vector2I submitted&&submitted!=_humanCell;
        HumanMessage(changedSite?$"原地块 ({_humanSubmittedCell!.Value.X}, {_humanSubmittedCell.Value.Y}) 的操作{(ok?"已受理":"未成功")}，详见操作记录。当前地块 ({_humanCell.X}, {_humanCell.Y}) 的条件已刷新。":result,ok);
        _humanHistory.Add($"第 {_humanSubmittedPeriod+1} 期"+(_humanSubmittedCell is Vector2I at?$" · ({at.X}, {at.Y})":"")+"\n"+result);
        if(_humanSubmittedOperation=="end_turn"&&ok){_humanEnded=true;_humanTurn=null;}
        if(receipt?["status"]?.ToString()=="confirmation_required")
        {
            _humanBusy=false;
            _afterHumanBatch=()=>HumanQuery("panel",new JsonObject { ["panel"]="war",["target"]=_warTarget },preview=>
            {
                var data=preview["data"]!;bool legal=data["legal"]?.GetValue<bool>()!=false;
                string names(JsonNode? rows)=>rows is JsonArray a?string.Join("、",a.Select(n=>NationLabel(S(n)))):"待确认";
                string warning=$"目标：{NationLabel(_warTarget)}\n攻方：{names(data["attackers"])}\n守方：{names(data["defenders"])}\n"+
                    (legal?"宣战将按联盟、保障和共同防御条约处理，外交结算后才生效。\n未知敌军与经济实力仍然未知，请确认补给与后方安全。":"当前不能宣战："+S(data["reason"]));
                ConfirmHuman("宣战前确认",warning,()=>SubmitHuman("declare_war",new JsonObject { ["target"]=_warTarget,["confirm"]=true }));
                _humanConfirm.GetOkButton().Disabled=!legal;
            });
        }
        else if(!processed){_humanBusy=false;_humanCommands.Clear();RefreshHumanQueue();RefreshHumanButtons();}
        return true;
    }
    private string _warTarget="";
    private Action? _afterHumanBatch;
    private void SubmitHuman(string operation,JsonObject arguments)
    {
        if(QueueableHuman(operation)){QueueHumanCommand(operation,arguments);return;}
        if(!HumanCanWrite){HumanMessage("当前不能提交：请先完成演出或继续游戏。",false);return;}
        if(_humanCommands.Count>0){HumanMessage("请先完成或取消待执行指令。",false);return;}
        SendHumanCommand(operation,arguments);
    }
    private void SendHumanCommand(string operation,JsonObject arguments)
    {
        if(operation=="declare_war")_warTarget=S(arguments["target"]);
        _humanSubmittedOperation=operation;_humanSubmittedPeriod=I(_humanTurn!["period"]);
        _humanSubmittedCell=arguments["x"]!=null&&arguments["y"]!=null?new Vector2I(I(arguments["x"]),I(arguments["y"])):null;
        if(operation=="demolish")_humanSubmittedCell=_humanCell;
        _humanWrite="write/"+Guid.NewGuid().ToString("N");_humanOperation=operation;_humanBusy=true;_humanResultReady=false;
        CancelHumanTarget();RefreshHumanButtons();
        SendHumanRow(new JsonObject { ["command"]="human_submit",["actor"]=HumanActor,["period"]=_humanTurn!["period"]!.DeepClone(),
            ["control_generation"]=_humanTurn["control_generation"]!.DeepClone(),["seat_token"]=_humanTurn["seat_token"]!.DeepClone(),
            ["request_id"]=_humanWrite,["operation"]=operation,["arguments"]=arguments });
    }
    private Action? _humanConfirmation;
    private void ConfirmHuman(string title,string message,Action action)
    {
        _humanConfirmation=action;_humanConfirm.Title=title;_humanConfirm.DialogText=message;
        _humanConfirm.GetOkButton().Disabled=false;
        _humanConfirm.PopupCentered(new Vector2I((int)Math.Min(620,Size.X-48),(int)Math.Min(280,Size.Y-48)));
    }
    private void RunHumanConfirmation(){var action=_humanConfirmation;_humanConfirmation=null;action?.Invoke();}
    private void HumanEndTurn()
    {
        if(!HumanCanWrite)return;
        var pending=_humanState!["pending_end"]!;
        int offers=pending["offers"]!.AsArray().Count,votes=pending["votes"]!.AsArray().Count;
        if(offers+votes>0)ConfirmHuman("结束本国回合",$"还有 {offers} 项外交提议、{votes} 项未表态投票。\n结束后自动拒绝未处理提议、对开放投票投反对票。",()=>SubmitHuman("end_turn",new JsonObject()));
        else SubmitHuman("end_turn",new JsonObject());
    }
    private void HumanMessage(string message,bool success=true)
    {
        _humanReceipt.Text=message;_humanReceipt.TooltipText=message;
        _humanReceipt.Modulate=new Color(success?"c6e3ad":"ffb6a4");
    }
    private void CancelHumanTarget(bool suspend=false)
    { _targetEpoch++;_humanTargetMode="";_manualArmyTarget=false;_armyRangeSuspended=suspend;_armyRangeDirty=true;_map.SetHumanTargets("",null);_targetSummary.Visible=false;_map.TooltipText=""; }
    private void HumanTileSelected(JsonObject? tile)
    {
        if(tile==null)return;
        var p=tile["position"]!;var point=new Vector2I(I(p[0]),I(p[1]));
        if(_manualArmyTarget&&_humanTargetMode!="")
        {
            if(_map.HumanTarget(point) is not JsonObject target){HumanMessage("请选择当前军令的目标圈；Esc 可取消。",false);return;}
            string mode=_humanTargetMode;
            SubmitHuman(mode,new JsonObject { ["ids"]=new JsonArray(_selectedArmies.Order().Select(i=>(JsonNode?)JsonValue.Create(i)).ToArray()),["x"]=point.X,["y"]=point.Y });
            _humanCell=point;return;
        }
        bool changed=_humanCell!=point;_humanCell=point;
        SetHumanSelectionCell(point);
        if(changed)HumanMessage("已切换地块；当前操作条件已刷新。");
        OpenHumanPage(_humanPage is "build" or "recruit"&&S(tile["owner"])==HumanActor?_humanPage:"tile");
    }
    private void StartHumanTarget(string mode,bool manual=true)
    {
        SetHumanSelectionCell(_selectionCell);
        if(!HumanCanWrite||_selectedArmies.Count==0){if(manual)HumanMessage("先在右侧军队总览勾选军队。",false);return;}
        _manualArmyTarget=manual;_armyRangeDirty=false;
        if(manual)_armyRangeSuspended=false;
        _humanTargetMode=mode;
        _map.SetHumanTargets(mode,null);
        int epoch=++_targetEpoch;
        var ids=new JsonArray(_selectedArmies.Order().Select(i=>(JsonNode?)JsonValue.Create(i)).ToArray());
        HumanQuery("targets",new JsonObject { ["ids"]=ids,["mode"]=mode },row=>
        {
            if(_humanTargetMode!=mode||epoch!=_targetEpoch)return;
            var data=row["data"]!;_map.SetHumanTargets(mode,data["targets"]!.AsArray());
            if(manual)_map.FocusHumanTargets();
            _targetSummary.Visible=_manualArmyTarget||_humanPage=="selected-armies";
            _targetSummary.Text=$"{(mode=="attack"?"攻击":mode=="retreat"?"撤退":"移动")} · {_map.TargetCount} 个候选格\n"+(manual?"左键选择目标；Esc 取消。":"右键移动 · Ctrl右键攻击 · Shift追加路线；虚线表示通路未确认。");
            if(_map.TargetCount==0)_targetSummary.Text+="\n"+string.Join("；",data["units"]!.AsArray().Select(u=>S(u!["reason"])).Where(r=>r!="").Distinct().Take(3));
        });
    }
}
