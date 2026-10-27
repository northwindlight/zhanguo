using Godot;
using System;
using System.Linq;
using System.Threading.Tasks;
using static WorldMap;

public partial class Desktop
{
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
        await PointerClick(_outlinerRows[destination]);
        if(!_selectedArmies.SetEquals(front)||_humanPage!="army-unit")throw new Exception("总览未直接选择前线军队");
        await PointerClick(_outlinerArmy);await ToSignal(GetTree().CreateTimer(.3),SceneTreeTimer.SignalName.Timeout);
        await PointerClick(_outlinerRows[capital]);
        if(!_selectedArmies.SetEquals(home)||_selectedArmies.Intersect(front).Any())throw new Exception("选择另一驻地时混入旧选择");
        foreach(string mode in new[]{"move","attack","retreat"})
            if(!((Button)_humanContent.FindChild("HumanOrder_"+mode,true,false)!).Disabled)throw new Exception("新募驻军仍亮起不可用军令");
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
        if(_selectedArmies.Except(home).Any())throw new Exception("切换地块没有清理驻地选择");
        _outlinerFilters.Hide();OpenHumanOutliner(true);
    }
}
