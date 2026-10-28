using Godot;
using System;
using System.IO;
using System.Linq;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using static WorldMap;

public partial class Desktop
{
    private async Task ReviewMapTools(string stem)
    {
        _humanCell=new Vector2I(I(OutlinerTiles.First()["position"]![0]),I(OutlinerTiles.First()["position"]![1]));
        OpenHumanPage("build");await RtsReady();
        if(_humanContent.FindChild("RtsTerrain",true,false) is not Label ground||!ground.Text.Contains("地形惩罚"))throw new Exception("建设面板未显示本格地形");
        var row=_rtsData!["buildings"]!.AsArray().First(b=>b!["price"]!=null)!;
        if(F(row["cost_breakdown"]!.AsArray().Last()!["after"])!=F(row["costs"]?["native:gold"]))throw new Exception("价格分项与实际金币不符");
        int writes=_humanHistory.Count;string frame=_map.Frame!.ToJsonString();float zoom=_map.Zoom;int terrainBuilds=_map.TerrainBuilds;
        await PointerClick(_terrainModeButton);
        if(!_map.SimpleTerrain||_map.Zoom!=zoom||frame!=_map.Frame.ToJsonString()||terrainBuilds!=_map.TerrainBuilds)throw new Exception("地形切换改变了镜头、事实或重建地表");
        if(_map.SimpleVisibleTiles+_map.UnknownCount!=_map.TileCount)throw new Exception("简易地图未按已知地形范围绘制");
        CloseDrawers(false);_map.Fit();await Screenshot(stem+"-simple-map.png");
        await PointerClick(_guideButton);
        await WaitFor(()=>_humanContent.FindChild("GuideBody",true,false) is RichTextLabel t&&t.GetParsedText().Contains("建设"),"说明书未通过玩家API读取");
        foreach(var (index,title) in new[]{(1,"基础指南"),(2,"经济学手册"),(3,"战争手册"),(4,"战略手册")})
        {
            _guidePicker.Select(index);_guidePicker.EmitSignal(OptionButton.SignalName.ItemSelected,(long)index);
            await WaitFor(()=>_humanContent.FindChild("GuideBody",true,false) is RichTextLabel t&&t.GetParsedText().Contains(title),"指南未返回："+title);
            ValidateHumanLayout();
        }
        _humanScroll.ScrollVertical=2000;await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
        if(!new Rect2(Vector2.Zero,Size).Encloses(_guideTools.GetGlobalRect()))throw new Exception("长文滚动后指南返回按钮不可见");
        await Screenshot(stem+"-guide.png");await PointerClick((Button)_guideTools.FindChild("GuideBack",true,false)!);await RtsReady();
        if(_humanPage!="build"||_humanHistory.Count!=writes)throw new Exception("指南返回丢失操作页或执行了动作");
        await PointerClick(_save);await WaitFor(()=>_humanPaused,"设置未暂停");
        await PointerClick(_rightReturnToggle);
        if(!_rightClickReturn||JsonNode.Parse(File.ReadAllText(DisplaySettings))?["right_click_return"]?.GetValue<bool>()!=true)throw new Exception("右键返回未保存");
        await Screenshot(stem+"-map-settings.png");await PointerClick(_menuContinue);await HumanIdle();
        _simpleTerrain=false;_rightClickReturn=false;LoadDisplayOptions();
        if(!_simpleTerrain||!_rightClickReturn)throw new Exception("地图／右键偏好未恢复");
        OpenHumanPage("build");await RtsReady();OpenHumanPage("tile");CloseDrawers(false);
        _map.SelectAndFocus(_humanCell);await RightOrderAt(_humanCell);await RtsReady();
        if(_humanPage!="build"||!_humanDrawer.IsOpen||_humanHistory.Count!=writes)throw new Exception("右键返回未打开上次页面，或自动执行了操作");
        foreach(var size in new[]{new Vector2I(1920,1080),new Vector2I(1366,768),new Vector2I(1280,720),new Vector2I(1024,768)})
        {
            GetWindow().ContentScaleSize=size;GetWindow().Size=size;CloseDrawers(false);_map.FocusNation(HumanActor);
            await ToSignal(GetTree().CreateTimer(.2),SceneTreeTimer.SignalName.Timeout);
            LayoutMapTools();
            var viewport=new Rect2(Vector2.Zero,Size);
            if(!viewport.Encloses(_mapTools.GetGlobalRect())||!viewport.Encloses(_terrainLegend.GetGlobalRect())||_terrainLegend.GetGlobalRect().Intersects(_ledgerPanel.GetGlobalRect()))throw new Exception("地图图例越界或覆盖资源面板");
            OpenHumanOutliner(false);await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);CheckOutlinerLayout();
            if(_outlinerRows.Values.Any(b=>b.Size.Y>70||b.FindChildren("LandStatus_*","TextureRect",true,false).Count!=4))throw new Exception("地块仍使用大图布局或缺少状态图标");
            await Screenshot(stem+$"-simple-{size.X}.png");
        }
        GetWindow().ContentScaleSize=new Vector2I(1920,1080);GetWindow().Size=new Vector2I(1920,1080);
        _rightClickReturn=false;_simpleTerrain=false;ApplyDisplayOptions(true);CloseDrawers(false);_map.FocusNation(HumanActor);
    }
    private async Task ReviewSpaceFilters(string stem,Vector2I at)
    {
        _outlinerFilters.Show();OpenHumanOutliner(false);
        var toggle=(CheckButton)_outlinerFilters.FindChild("OutlinerFreeSpace",true,false)!;await PointerClick(toggle);
        OutlinerPick("OutlinerResource","native:farmland");
        var expected=HumanFacts["building_sites"]!["capacities"]!.AsArray().Where(s=>s!["resources"]!.AsArray().Any(r=>S(r!["resource"])=="native:farmland"&&F(r["free"])>0))
            .Select(s=>new Vector2I(I(s!["position"]![0]),I(s["position"]![1]))).ToHashSet();
        if(!expected.SetEquals(_outlinerRows.Keys))throw new Exception("耕地＋空位筛选与正式预占不符");
        await Screenshot(stem+"-free-farmland.png");
        OutlinerPick("OutlinerResource","");await PointerClick((CheckButton)_outlinerFilters.FindChild("OutlinerFreeSpace",true,false)!);
        if(OutlinerSpace(at) is not JsonObject space||I(space["construction"])<1||I(space["pending"])<1||!_outlinerRows[at].TooltipText.Contains("待建 1"))throw new Exception("预付建筑没有进入空间／状态显示");
        _outlinerFilters.Hide();_humanCell=at;OpenHumanPage("build");await RtsReady();
        var selected=_selectedArmies.ToArray();_selectedArmies.Clear();ArmySelectionChanged();
        bool preference=_rightClickReturn;_rightClickReturn=true;int calls=_humanHistory.Count;
        OpenHumanPage("tile");CloseDrawers(false);_map.SelectAndFocus(at);await RightOrderAt(at);await RtsReady();
        await WaitFor(()=>new Rect2(_humanScroll.GlobalPosition,_humanScroll.Size).Encloses(_rtsCards["native:castle"].GetGlobalRect()),"右键没有回到上次实际点击的城堡按钮");
        if(_humanHistory.Count!=calls)throw new Exception("返回上次按钮自动执行了建设");
        _rightClickReturn=preference;foreach(int id in selected)_selectedArmies.Add(id);ArmySelectionChanged();
    }
}
