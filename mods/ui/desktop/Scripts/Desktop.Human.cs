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
    private bool _humanBusy,_humanPaused,_humanStarted,_humanEnded,_humanNeedsFrame;
    private string _humanOperation="",_humanPage="",_humanWrite="",_humanTargetMode="";
    private Control? _observerDock,_mapHint;
    private HBoxContainer _humanDock=null!;
    private MapDrawer _humanDrawer=null!;
    private VBoxContainer _humanContent=null!;
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
        _targetSummary=new Label { Visible=false,AutowrapMode=TextServer.AutowrapMode.WordSmart,Modulate=new Color("a9cddd") };
        _humanDrawer.Body.AddChild(_targetSummary);
        var scroll=new ScrollContainer { SizeFlagsVertical=SizeFlags.ExpandFill,HorizontalScrollMode=ScrollContainer.ScrollMode.Disabled };
        _humanDrawer.Body.AddChild(scroll);
        _humanContent=new VBoxContainer { SizeFlagsHorizontal=SizeFlags.ExpandFill };
        _humanContent.AddThemeConstantOverride("separation",10);scroll.AddChild(_humanContent);
        _humanReceipt=new Label { Text="点地块查看驻军与建设；操作后在这里显示实际回执。",AutowrapMode=TextServer.AutowrapMode.WordSmart,
            MaxLinesVisible=5,TextOverrunBehavior=TextServer.OverrunBehavior.TrimEllipsis,Modulate=new Color("c6d4b4") };
        _humanDrawer.Body.AddChild(_humanReceipt);
        _humanConfirm=new ConfirmationDialog { Title="确认操作",DialogText="",MinSize=new Vector2I(460,180),OkButtonText="确认",CancelButtonText="返回",Theme=Theme,DialogAutowrap=true };
        AddChild(_humanConfirm);_humanConfirm.Confirmed+=RunHumanConfirmation;
        BuildHumanExit();
        _map.HumanTargetHovered=target=>
        {
            if(_humanTargetMode=="")return;
            _targetSummary.Text=target==null?"选择地图上的目标圈；Esc 取消军令。":
                $"本次可执行 {target["ids"]!.AsArray().Count} / 已选 {_selectedArmies.Count} 支"+
                (target["uncertain"]?.GetValue<bool>()==true?" · 迷雾内尝试，通路尚未确认":"")+
                string.Concat((target["excluded"]?.AsArray()??new JsonArray()).Select(e=>$"\n#{e!["aid"]}：{e["reason"]}"));
            _map.TooltipText=_targetSummary.Text;
        };
    }
    private void LayoutHuman()
    {
        if(_humanDrawer==null)return;
        float width=Math.Min(520,Size.X-328),height=Math.Max(180,Size.Y-300);
        _humanDrawer.SetBounds(new Rect2(Size.X-width-16,216,width,height));
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
        if(_humanTurn!=null&&I(_humanTurn["period"])==I(state["period"]))_humanNeedsFrame=false;
        _desktopTitle.Text="战国 · "+NationLabel(HumanActor);
        _menuQuit.Text="退出游戏…";_menuContinue.Text="继续操作 · Esc";
        _humanDock.Visible=true;_observerDock?.Hide();_mapHint?.Hide();_bubbles.HideAll();_step.Visible=false;
        _humanNav["diplomacy"].Text="外交"+Badge(state["pending_end"]?["offers"]?.AsArray().Count??0);
        _humanNav["alliance"].Text="联盟"+Badge(state["pending_end"]?["votes"]?.AsArray().Count??0);
        _humanNav["intel"].Text="情报"+Badge(HumanFacts["intel"]?.AsArray().Count(m=>!_readIntel.Contains(I(m!["id"])))??0);
        _selectedArmies.RemoveWhere(id=>!OwnArmies.Any(u=>I(u!["id"])==id));
        if(!_humanStarted)
        {
            _humanStarted=true;_playbackPaused=false;Send("resume");
            _map.FocusNation(HumanActor);
        }
        if(_humanDrawer.IsOpen&&_humanTargetMode=="")RenderHumanPage();
        RefreshHumanButtons();
    }
    private static string Badge(int count)=>count>0?$" ({count})":"";
    private IEnumerable<JsonNode> OwnArmies=>(_humanState?["facts"]?["army"]?.AsArray()??new JsonArray()).Where(u=>S(u!["owner"])==HumanActor).Select(u=>u!);
    private void RefreshHumanButtons()
    {
        if(_humanNav.Count==0||!HumanMode)return;
        _humanNav["end"].Disabled=!HumanCanWrite;
        foreach(var button in _humanWrites)if(IsInstanceValid(button))button.Disabled=!HumanCanWrite;
        if(_humanSubmit!=null&&IsInstanceValid(_humanSubmit))_humanSubmit.Disabled=!HumanCanWrite||!HumanFormReady();
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
        var receipt=row["data"] as JsonObject;
        bool ok=processed&&receipt?["ok"]?.GetValue<bool>()==true;
        _humanReceipt.Modulate=new Color(ok?"c6e3ad":"ffb6a4");
        string result=processed?FormatHumanReceipt(receipt!):S(row["message"]);
        HumanMessage(result,ok);_humanHistory.Add(result);
        if(_humanOperation=="end_turn"&&ok){_humanEnded=true;_humanTurn=null;}
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
        else if(!processed){_humanBusy=false;RefreshHumanButtons();}
        return true;
    }
    private string _warTarget="";
    private Action? _afterHumanBatch;
    private void SubmitHuman(string operation,JsonObject arguments)
    {
        if(!HumanCanWrite){HumanMessage("当前不能提交：请先完成演出或继续游戏。",false);return;}
        if(operation=="declare_war")_warTarget=S(arguments["target"]);
        _humanWrite="write/"+Guid.NewGuid().ToString("N");_humanOperation=operation;_humanBusy=true;
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
    private void CancelHumanTarget()
    { _humanTargetMode="";_map.SetHumanTargets("",null);_targetSummary.Visible=false;_map.TooltipText=""; }
    private void HumanTileSelected(JsonObject? tile)
    {
        if(tile==null)return;
        var p=tile["position"]!;var point=new Vector2I(I(p[0]),I(p[1]));
        if(_humanTargetMode!="")
        {
            if(_map.HumanTarget(point) is not JsonObject target){HumanMessage("请选择当前军令的目标圈；Esc 可取消。",false);return;}
            string mode=_humanTargetMode;
            SubmitHuman(mode,new JsonObject { ["ids"]=new JsonArray(_selectedArmies.Order().Select(i=>(JsonNode?)JsonValue.Create(i)).ToArray()),["x"]=point.X,["y"]=point.Y });
            _humanCell=point;return;
        }
        _humanCell=point;OpenHumanPage("tile");
    }
    private void StartHumanTarget(string mode)
    {
        if(!HumanCanWrite||_selectedArmies.Count==0){HumanMessage("先在地块驻军面板勾选本国军队。",false);return;}
        _humanTargetMode=mode;
        var ids=new JsonArray(_selectedArmies.Order().Select(i=>(JsonNode?)JsonValue.Create(i)).ToArray());
        HumanQuery("targets",new JsonObject { ["ids"]=ids,["mode"]=mode },row=>
        {
            if(_humanTargetMode!=mode)return;
            var data=row["data"]!;_map.SetHumanTargets(mode,data["targets"]!.AsArray());
            _map.FocusHumanTargets();
            _targetSummary.Visible=true;
            _targetSummary.Text=$"{(mode=="attack"?"攻击":mode=="retreat"?"撤退":"移动")} · {_map.TargetCount} 个候选格\n空心蓝圈为移动，红色准星为攻击；虚线表示未确认通路。Esc 取消。";
        });
    }
}
