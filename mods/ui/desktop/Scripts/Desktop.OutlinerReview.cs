using Godot;
using System;
using System.Linq;
using System.Threading.Tasks;
using static WorldMap;

public partial class Desktop
{
    private async Task RightOrderAt(Vector2I point,bool attack=false,bool append=false)
    {
        var at=_map.CellCenter(point);
        GetViewport().PushInput(new InputEventMouseButton { ButtonIndex=MouseButton.Right,Pressed=true,Position=at,GlobalPosition=at,CtrlPressed=attack,ShiftPressed=append },true);
        await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
        GetViewport().PushInput(new InputEventMouseButton { ButtonIndex=MouseButton.Right,Pressed=false,Position=at,GlobalPosition=at,CtrlPressed=attack,ShiftPressed=append },true);
        await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
    }
    private async Task ReviewResourceTooltip()
    {
        HideResourceTooltip();
        var control=_resourceHoverRows["native:wood"];
        GetViewport().PushInput(new InputEventMouseMotion { Position=new Vector2(340,600),GlobalPosition=new Vector2(340,600) },true);
        await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
        var at=control.GetGlobalRect().GetCenter();
        GetViewport().PushInput(new InputEventMouseMotion { Position=at,GlobalPosition=at },true);
        await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
        if(_resourceTooltip?.Visible!=true||!new Rect2(Vector2.Zero,Size).Encloses(_resourceTooltip.GetGlobalRect()))throw new Exception("资源明细没有随悬停展开，或超出窗口");
        var row=Zhanguo.Desktop.Layout.NationLedger.Read(_map.Frame!,HumanActor)!.Stocks.First(s=>s.Id=="native:wood");
        if(!row.Contributions.Any(c=>c.Kind=="native:wood-power-plant"&&c.Count==1&&c.Amount==-1)||Math.Abs(row.Contributions.Sum(c=>c.Amount)-row.Change)>1e-8)throw new Exception("木材明细没有实际电厂消耗，或与净变化不符");
    }
    // Actual key events, deliberately no Enter or focus change before reading.
    private async Task TypeNumber(SpinBox field,string text)
    {
        for(Node? p=field.GetParent();p!=null;p=p.GetParent())if(p is ScrollContainer scroll)scroll.EnsureControlVisible(field);
        await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
        var edit=field.GetLineEdit();await PointerClick(edit);edit.GrabFocus();edit.SelectAll();
        foreach(char c in text)
        {
            GetViewport().PushInput(new InputEventKey { Keycode=(Key)c,Unicode=c,Pressed=true },true);
            GetViewport().PushInput(new InputEventKey { Keycode=(Key)c,Unicode=c,Pressed=false },true);
        }
        await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
        if(!double.TryParse(text,System.Globalization.NumberStyles.Float,System.Globalization.CultureInfo.InvariantCulture,out double expected)||Math.Abs(field.Value-expected)>.000001)
            throw new Exception($"数字输入仍须回车：{field.Name} 显示{edit.Text}，取值{field.Value}，期望{text}");
    }
    private void OutlinerPick(string name,string value)
    {
        var pick=(OptionButton)_outlinerFilters.FindChild(name,true,false)!;
        var ids=name switch {
            "OutlinerBuilding"=>HumanFacts["building_catalog"]!.AsArray().Select(b=>S(b!["id"])),
            "OutlinerTerrain"=>HumanFacts["terrain_catalog"]!.AsArray().Select(t=>S(t!["id"])),
            "OutlinerResource"=>OutlinerTiles.SelectMany(t=>t["resources"]!.AsArray()).Select(r=>S(r![0])).Distinct().Order(),
            _=>throw new Exception("没有验收筛选器") };
        int index=Array.IndexOf(new[]{""}.Concat(ids).ToArray(),value);
        if(index<0)throw new Exception("筛选值不存在："+value);
        pick.Select(index);pick.EmitSignal(OptionButton.SignalName.ItemSelected,(long)index);
    }
    private void CheckOutlinerLayout()
    {
        var viewport=new Rect2(Vector2.Zero,Size);
        if(!_outliner.IsVisibleInTree()||!viewport.Encloses(_outliner.GetGlobalRect()))throw new Exception("右侧总览不在窗口内");
        foreach(var b in new[]{_outlinerLand,_outlinerArmy})if(!viewport.Encloses(b.GetGlobalRect()))throw new Exception("总览标签不在窗口内");
        if(_humanDrawer.IsOpen&&Size.X>=1500&&_outlinerBody.Visible&&_humanDrawer.GetGlobalRect().Intersects(_outliner.GetGlobalRect()))throw new Exception("宽屏总览与操作抽屉重叠");
        if(_banner.GetGlobalRect().Intersects(_outliner.GetGlobalRect()))throw new Exception("总览遮住国家横幅");
        if(_outlinerScroll.GetHScrollBar().MaxValue>_outlinerScroll.GetHScrollBar().Page+1)throw new Exception("右侧总览横向溢出");
    }
    private async Task ReviewHumanOutliner(string stem,Vector2I capital,Vector2I destination)
    {
        // Create a second real squad at another site; normal opening remains zero-army.
        _humanCell=capital;OpenHumanPage("recruit");await RtsReady();
        await PointerClick(_rtsCards["native:militia"]);await HumanIdle();
        var home=OwnArmies.Where(u=>I(u["x"])==capital.X&&I(u["y"])==capital.Y).Select(u=>I(u["id"])).ToArray();
        var front=OwnArmies.Where(u=>I(u["x"])==destination.X&&I(u["y"])==destination.Y).Select(u=>I(u["id"])).ToArray();
        if(home.Length==0||front.Length==0)throw new Exception("未产生两处实际驻军");
        await PointerClick(_humanNav["army"]);await ToSignal(GetTree().CreateTimer(.3),SceneTreeTimer.SignalName.Timeout);
        CheckOutlinerLayout();if(_outlinerRows.Count!=2)throw new Exception("军队总览未列出两处驻军");
        await Screenshot(stem+"-outliner-armies.png");
        _selectedArmies.Clear();ArmySelectionChanged();
        await PointerClick(_outlinerRows[destination]);
        if(!_selectedArmies.SetEquals(front))throw new Exception("总览未选择前线军队");
        await PointerClick(_outlinerRows[capital]);
        if(!_selectedArmies.SetEquals(home.Concat(front)))throw new Exception("跨驻地选择被清空");
        if(!_armySelectionText.Text.Contains("2 处驻地")||!_armySelectionPanel.Visible)throw new Exception("已选摘要没有固定显示");
        GetViewport().PushInput(new InputEventKey { Keycode=Key.Ctrl,Pressed=true },true);
        await WaitFor(()=>_humanTargetMode=="attack"&&_map.HumanTarget(destination)!=null,"跨驻地攻击没有候选");
        var partial=_map.HumanTarget(destination)!;
        if(partial["ids"]!.AsArray().Count!=front.Length||partial["excluded"]!.AsArray().Count!=home.Length)throw new Exception("新兵与前线部分可执行结果不一致");
        GetViewport().PushInput(new InputEventKey { Keycode=Key.Ctrl,Pressed=false },true);
        await PointerClick((Button)_armySelectionPanel.FindChild("ShowSelectedArmies",true,false)!);
        ValidateHumanLayout();
        await Screenshot(stem+"-outliner-orders.png");
        await PointerClick(_outlinerLand);await ToSignal(GetTree().CreateTimer(.3),SceneTreeTimer.SignalName.Timeout);
        if(_outlinerRows.Count!=OutlinerTiles.Count())throw new Exception("本国地块总览缺项");
        await PointerClick((Button)_outlinerBody.FindChild("OutlinerFilterToggle",true,false)!);
        OutlinerPick("OutlinerBuilding","native:militia-camp");
        if(!_outlinerRows.Keys.SequenceEqual(new[]{capital}))throw new Exception("建筑筛选不正确");
        await Screenshot(stem+"-outliner-building-filter.png");
        OutlinerPick("OutlinerBuilding","");
        string terrain=S(OutlinerTiles.First()["terrain"]);OutlinerPick("OutlinerTerrain",terrain);
        if(_outlinerRows.Count!=OutlinerTiles.Count(t=>S(t["terrain"])==terrain))throw new Exception("地形筛选不正确");
        OutlinerPick("OutlinerTerrain","");
        string resource=OutlinerTiles.SelectMany(t=>t["resources"]!.AsArray()).Where(r=>F(r![1])>0).Select(r=>S(r![0])).First();
        OutlinerPick("OutlinerResource",resource);
        if(_outlinerRows.Count!=OutlinerTiles.Count(t=>t["resources"]!.AsArray().Any(r=>S(r![0])==resource&&F(r[1])>0)))throw new Exception("资源筛选不正确");
        OutlinerPick("OutlinerResource","");
        _outlinerSearch.Text="绝不存在的地块";_outlinerSearch.EmitSignal(LineEdit.SignalName.TextChanged,_outlinerSearch.Text);
        if(_outlinerRows.Count!=0)throw new Exception("搜索无匹配仍显示地块");
        _outlinerSearch.Text="";_outlinerSearch.EmitSignal(LineEdit.SignalName.TextChanged,"");
        await PointerClick(_outlinerRows[capital]);
        if(!_selectedArmies.SetEquals(home.Concat(front)))throw new Exception("切换地块丢失跨驻地勾选");
        _outlinerFilters.Hide();OpenHumanOutliner(true);
        await PointerClick((Button)_armySelectionPanel.FindChild("ClearArmySelection",true,false)!);
        await PointerClick((CheckButton)_outlinerList.FindChild("OutlinerArmy_"+home[0],true,false)!);
        CloseDrawers(false);_map.SelectAndFocus(capital);
        await ToSignal(GetTree().CreateTimer(.2),SceneTreeTimer.SignalName.Timeout);
        if(_map.SelectedSquadUnits<1)throw new Exception("选中的实际编队没有脚底光环");
        await Screenshot(stem+"-selected-halo.png");
        // Renderer-only fixture: an extra unselected unit at the same site.
        // The running game and its army identities are never changed.
        var actual=_map.Frame!.DeepClone().AsObject();var mixed=actual.DeepClone().AsObject();
        foreach(var array in new[]{mixed["armies"]!.AsArray(),mixed["scene"]!["armies"]!.AsArray()})
        {
            var copy=array.First(u=>S(u!["owner"])==HumanActor&&I(u["id"])==home[0])!.DeepClone();copy["id"]=99999;array.Add(copy);
        }
        _map.SetFrame(mixed);await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
        if(_map.SelectedSquadUnits!=1)throw new Exception("同格未选中的军队也出现了选中光环");
        await Screenshot(stem+"-partial-selection-halo.png");_map.SetFrame(actual);
        var waypoint=OutlinerTiles.Select(t=>new Vector2I(I(t["position"]![0]),I(t["position"]![1]))).First(p=>p!=capital);
        _map.SelectAndFocus(waypoint);await RightOrderAt(waypoint,append:true);await HumanIdle();
        var wild=_map.Frame!["tiles"]!.AsArray().First(t=>S(t!["visibility"])=="live"&&t["owner"]==null&&t["terrain"]!=null&&S(t["terrain"]) is not "native:high-mountain" and not "native:ocean")!;
        var second=new Vector2I(I(wild["position"]![0]),I(wild["position"]![1]));
        _map.SelectAndFocus(second);await RightOrderAt(second,attack:true,append:true);await HumanIdle();
        var itinerary=_humanState!["army_orders"]!.AsArray().Single(r=>I(r!["aid"])==home[0])!;
        if(itinerary["nodes"]!.AsArray().Count!=2||S(itinerary["nodes"]![1]!["mode"])!="attack"||_map.OrderNodeCount!=2)throw new Exception("两种Shift右键没有追加节点和路径线");
        OpenHumanPage("selected-armies");await WaitFor(()=>Drawers.All(d=>d.IsSettled),"路线面板未展开");ValidateHumanLayout();await Screenshot(stem+"-march-waypoints.png");
        OpenHumanOutliner(true);
    }
}
