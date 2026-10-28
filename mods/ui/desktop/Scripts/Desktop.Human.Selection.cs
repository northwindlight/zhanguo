using Godot;
using System;
using System.Linq;
using System.Text.Json.Nodes;
using static WorldMap;

public partial class Desktop
{
    private PanelContainer _armySelectionPanel=null!;
    private Label _armySelectionText=null!;
    private Button _onlySelectedToggle=null!;
    private bool _onlySelectedArmies,_autoHealOrders=true,_replaceNextRoute;
    private bool _manualArmyTarget,_armyRangeDirty=true,_armyRangeSuspended,_armyCtrlHeld;
    private JsonObject? _lastArmyTarget;
    private JsonArray SelectedIds()=>new(_selectedArmies.Order().Select(i=>(JsonNode?)JsonValue.Create(i)).ToArray());

    private void BuildArmySelection(VBoxContainer parent)
    {
        _armySelectionPanel=new PanelContainer { Name="ArmySelectionSummary",Visible=false };parent.AddChild(_armySelectionPanel);
        _armySelectionPanel.AddThemeStyleboxOverride("panel",HumanArt.Box("panel"));
        var body=new VBoxContainer();body.AddThemeConstantOverride("separation",3);_armySelectionPanel.AddChild(body);
        _armySelectionText=new Label { Name="ArmySelectionText",AutowrapMode=TextServer.AutowrapMode.WordSmart,MaxLinesVisible=2,TextOverrunBehavior=TextServer.OverrunBehavior.TrimEllipsis };
        _armySelectionText.AddThemeFontSizeOverride("font_size",14);body.AddChild(_armySelectionText);
        var buttons=new HBoxContainer();body.AddChild(buttons);
        var view=Button(buttons,"查看已选",()=>OpenHumanPage("selected-armies"));view.Name="ShowSelectedArmies";
        var filter=_onlySelectedToggle=Button(buttons,"仅已选",()=>{_onlySelectedArmies=!_onlySelectedArmies;RenderOutlinerRows();});filter.ToggleMode=true;filter.Name="OnlySelectedArmies";
        var clear=Button(buttons,"清空",()=>{_selectedArmies.Clear();ArmySelectionChanged();if(_humanDrawer.IsOpen)RenderHumanPage();});clear.Name="ClearArmySelection";
        foreach(var button in new[]{view,filter,clear}){HumanArt.Style(button);button.AddThemeFontSizeOverride("font_size",13);button.SizeFlagsHorizontal=SizeFlags.ExpandFill;}
        _map.HumanMapOrder=HumanMapOrder;
    }
    private void ToggleArmy(int aid,bool selected)
    {
        if(selected)_selectedArmies.Add(aid);else _selectedArmies.Remove(aid);
        ArmySelectionChanged();
    }
    private void ArmySelectionChanged()
    {
        _replaceNextRoute=false;_lastArmyTarget=null;CancelHumanTarget();RefreshArmySelection();RenderOutlinerRows();
    }
    private void RefreshArmySelection()
    {
        var chosen=OwnArmies.Where(u=>_selectedArmies.Contains(I(u["id"]))).ToArray();
        if(chosen.Length==0)_onlySelectedArmies=false;
        _onlySelectedToggle.SetPressedNoSignal(_onlySelectedArmies);
        _armySelectionPanel.Visible=chosen.Length>0;
        _armySelectionText.Text=$"已选 {chosen.Length} 支／{chosen.Select(u=>(I(u["x"]),I(u["y"]))).Distinct().Count()} 处驻地\n"+
            string.Join(" · ",chosen.GroupBy(u=>S(u["kind"])).Select(g=>HumanLabel(g.Key)+"×"+g.Count()));
        _armySelectionText.TooltipText=string.Join("\n",chosen.Select(u=>$"#{u["id"]} {HumanLabel(S(u["kind"]))} ({u["x"]},{u["y"]}) ♥{u["hp"]}"));
        _map.SetArmySelection(_selectedArmies,_humanState?["army_orders"] as JsonArray);
        LayoutHumanOutliner();
    }
    private void TickArmySelection()
    {
        if(!HumanCanWrite||_selectedArmies.Count==0||_armyRangeSuspended)return;
        string mode=_armyCtrlHeld||Input.IsKeyPressed(Key.Ctrl)?"attack":"move";
        if(_manualArmyTarget){if(mode!="attack")return;_manualArmyTarget=false;}
        if(_armyRangeDirty||_humanTargetMode!=mode)StartHumanTarget(mode,false);
    }
    private bool HumanMapOrder(Vector2I at,bool attack,bool append)
    {
        if(!HumanMode)return false;
        if(_selectedArmies.Count==0)return ReturnToLastOperation();
        if(append)
        {
            SubmitHuman("queue_orders",new JsonObject { ["ids"]=SelectedIds(),
                ["nodes"]=new JsonArray(new JsonObject { ["mode"]=attack?"attack":"move",["position"]=new JsonArray(at.X,at.Y) }),
                ["append"]=!_replaceNextRoute,["auto_heal"]=_autoHealOrders });
            _replaceNextRoute=false;
        }
        else {_replaceNextRoute=false;SubmitHuman(attack?"attack":"move",new JsonObject { ["ids"]=SelectedIds(),["x"]=at.X,["y"]=at.Y });}
        return true;
    }
    private void RenderSelectedArmies()
    {
        HumanText($"已选军队 · {_selectedArmies.Count} 支",true);
        HumanText("右键移动 · Ctrl右键攻击\nShift右键追加移动节点 · Shift Ctrl右键追加攻击节点\n中键拖动地图；切换地块和筛选保留勾选。",color:"b3c6ac");
        if(_lastArmyTarget is JsonObject target)HumanText($"上次悬停目标 ({target["position"]![0]},{target["position"]![1]})\n可执行编号："+string.Join("、",target["ids"]!.AsArray().Select(i=>"#"+S(i))),color:"a7cddd");
        var orders=new HBoxContainer();_humanContent.AddChild(orders);
        foreach(var (mode,title) in new[]{("move","移动"),("attack","攻击"),("retreat","撤退")})
        {
            string command=mode;var button=Button(orders,title,()=>StartHumanTarget(command));HumanArt.Style(button,mode);
            _rtsAvailability[button]=OwnArmies.Any(u=>_selectedArmies.Contains(I(u["id"]))&&UnitCan(u,command));
        }
        var heal=new CheckButton { Name="MarchAutoHeal",Text="低于30生命停下，恢复满血后续行",ButtonPressed=_autoHealOrders };
        heal.AddThemeFontSizeOverride("font_size",14);_humanContent.AddChild(heal);
        heal.Toggled+=on=>
        {
            _autoHealOrders=on;
            var existing=(_humanState?["army_orders"] as JsonArray??new JsonArray()).Where(r=>_selectedArmies.Contains(I(r!["aid"]))).Select(r=>(JsonNode?)JsonValue.Create(I(r!["aid"]))).ToArray();
            if(existing.Length>0)SubmitHuman("queue_orders",new JsonObject { ["ids"]=new JsonArray(existing),["nodes"]=new JsonArray(),["auto_heal"]=on });
        };
        HumanButton("重画已选路线（下一次Shift右键替换）",()=>{_replaceNextRoute=true;HumanMessage("下一次Shift右键替换已选军队的剩余路线；Esc取消重画。");},"move");
        HumanButton("取消已选军队的剩余路线",()=>SubmitHuman("cancel_orders",new JsonObject { ["ids"]=SelectedIds() }),"disband",true);
        foreach(var unit in OwnArmies.Where(u=>_selectedArmies.Contains(I(u["id"]))).ToArray())
        {
            int aid=I(unit["id"]);
            var line=new HBoxContainer();_humanContent.AddChild(line);
            var check=new CheckButton { Text=$"#{aid} {HumanLabel(S(unit["kind"]))} ({unit["x"]},{unit["y"]}) ♥{unit["hp"]}",ButtonPressed=true,SizeFlagsHorizontal=SizeFlags.ExpandFill };
            check.AddThemeFontSizeOverride("font_size",14);line.AddChild(check);check.Toggled+=on=>{ToggleArmy(aid,on);RenderHumanPage();};
            var excluded=_lastArmyTarget?["excluded"]?.AsArray().FirstOrDefault(r=>I(r!["aid"])==aid);
            if(excluded!=null)HumanText("未参与："+S(excluded["reason"]),color:"ddb49b");
            var locate=Button(line,"定位",()=>_map.SelectAndFocus(new(I(unit["x"]),I(unit["y"]))));HumanArt.Style(locate,"pin");
            var route=(_humanState?["army_orders"] as JsonArray??new JsonArray()).FirstOrDefault(r=>I(r!["aid"])==aid);
            if(route==null)continue;
            HumanText(S(route["reason"]) is {Length:>0} reason?reason:"按路线逐期执行",color:Yes(route["healing"])?"e8c87e":"a9c7ac");
            foreach(var node in route["nodes"]!.AsArray())
            {
                int index=I(node!["index"]);var row=new HBoxContainer();_humanContent.AddChild(row);
                var text=new Label { Text=$"{index}. {(S(node["mode"])=="attack"?"⚔":"→")} ({node["position"]![0]},{node["position"]![1]})"+(Yes(node["uncertain"])?" · 通路未确认":""),SizeFlagsHorizontal=SizeFlags.ExpandFill };
                text.AddThemeFontSizeOverride("font_size",14);row.AddChild(text);
                var remove=Button(row,"删除",()=>SubmitHuman("cancel_orders",new JsonObject { ["ids"]=new JsonArray(aid),["node"]=index }));HumanArt.Style(remove,"disband");_rtsAvailability[remove]=true;
            }
        }
    }
}
