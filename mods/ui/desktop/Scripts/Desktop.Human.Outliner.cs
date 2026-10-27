using Godot;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using static WorldMap;

public partial class Desktop
{
    private PanelContainer _outliner=null!;
    private VBoxContainer _outlinerBody=null!,_outlinerList=null!,_outlinerFilters=null!;
    private ScrollContainer _outlinerScroll=null!;
    private Button _outlinerLand=null!,_outlinerArmy=null!,_outlinerFold=null!;
    private LineEdit _outlinerSearch=null!;
    private Label _outlinerCount=null!;
    private bool _outlinerArmies,_outlinerCollapsed;
    private string _landSearch="",_armySearch="",_landTerrain="",_landResource="",_landBuilding="",_landState="",_armyKind="",_armyState="";
    private Vector2I _selectionCell=new(-1,-1);
    private int _targetEpoch;
    private readonly Dictionary<Vector2I,Button> _outlinerRows=new();

    private void BuildHumanOutliner()
    {
        _outliner=new PanelContainer { Name="HumanOutliner",Visible=false,ZIndex=21 };
        _outliner.AddThemeStyleboxOverride("panel",HumanArt.Box("panel"));AddChild(_outliner);
        var column=new VBoxContainer();column.AddThemeConstantOverride("separation",6);_outliner.AddChild(column);
        var tabs=new HBoxContainer();column.AddChild(tabs);
        _outlinerLand=Button(tabs,"地块",()=>OpenHumanOutliner(false));_outlinerLand.Name="OutlinerLand";
        _outlinerArmy=Button(tabs,"军队",()=>OpenHumanOutliner(true));_outlinerArmy.Name="OutlinerArmy";
        foreach(var b in new[]{_outlinerLand,_outlinerArmy}){b.SizeFlagsHorizontal=SizeFlags.ExpandFill;HumanArt.Style(b);b.AddThemeFontSizeOverride("font_size",16);}
        _outlinerFold=Button(tabs,"−",()=>
        {
            if(Size.X<1500&&_humanDrawer.IsOpen){CloseDrawers();_outlinerCollapsed=false;}
            else _outlinerCollapsed=!_outlinerCollapsed;
            LayoutHuman();
        });HumanArt.Style(_outlinerFold);
        _outlinerFold.TooltipText="展开／收起总览";
        _outlinerBody=new VBoxContainer { SizeFlagsVertical=SizeFlags.ExpandFill };column.AddChild(_outlinerBody);
        _outlinerSearch=new LineEdit { Name="OutlinerSearch",PlaceholderText="搜索名称、坐标、建筑",ClearButtonEnabled=true };_outlinerBody.AddChild(_outlinerSearch);
        _outlinerSearch.TextChanged+=text=>{if(_outlinerArmies)_armySearch=text;else _landSearch=text;RenderOutlinerRows();};
        var filter=Button(_outlinerBody,"筛选 ▾",()=>{_outlinerFilters.Visible=!_outlinerFilters.Visible;LayoutHumanOutliner();});HumanArt.Style(filter);filter.Name="OutlinerFilterToggle";
        _outlinerFilters=new VBoxContainer { Visible=false };_outlinerBody.AddChild(_outlinerFilters);
        _outlinerCount=new Label { Modulate=new Color("b5c7aa") };_outlinerCount.AddThemeFontSizeOverride("font_size",14);_outlinerBody.AddChild(_outlinerCount);
        _outlinerScroll=new ScrollContainer { SizeFlagsVertical=SizeFlags.ExpandFill,HorizontalScrollMode=ScrollContainer.ScrollMode.Disabled };_outlinerBody.AddChild(_outlinerScroll);
        _outlinerList=new VBoxContainer { SizeFlagsHorizontal=SizeFlags.ExpandFill };_outlinerList.AddThemeConstantOverride("separation",5);_outlinerScroll.AddChild(_outlinerList);
    }
    private void OpenHumanOutliner(bool armies)
    {
        if(!HumanMode)return;
        SaveHumanDraft();SaveMarketDraft();CancelHumanTarget();CloseDrawers();_humanPage=armies?"army":"land";_outlinerArmies=armies;_outlinerCollapsed=false;
        _outlinerSearch.Text=armies?_armySearch:_landSearch;_outlinerScroll.ScrollVertical=0;
        RenderHumanOutliner();
    }
    private void LayoutHumanOutliner()
    {
        if(_outliner==null)return;
        bool shown=HumanMode&&!_pauseMenu.Visible&&!HasTimeline;
        _outliner.Visible=shown;if(!shown)return;
        // Small windows keep the two tabs available while details use the space below.
        bool compact=_outlinerCollapsed||(Size.X<1500&&_humanDrawer.IsOpen);
        _outlinerBody.Visible=!compact;_outlinerFold.Text=compact?"+":"−";
        float width=Size.X<1200?246:280;
        _outliner.Position=new Vector2(Size.X-width-16,70);
        _outliner.CustomMinimumSize=Vector2.Zero;
        _outliner.Size=new Vector2(width,compact?44:Size.Y-154);
    }
    private void SetHumanSelectionCell(Vector2I at)
    {
        if(_selectionCell!=at){_selectedArmies.Clear();_selectionCell=at;_targetEpoch++;}
        _selectedArmies.RemoveWhere(id=>!OwnArmies.Any(u=>I(u["id"])==id&&I(u["x"])==at.X&&I(u["y"])==at.Y));
    }
    private void SelectOutlinerCell(Vector2I at,bool army,int[]? ids=null)
    {
        CancelHumanTarget();SetHumanSelectionCell(at);_humanCell=at;
        if(army)
        {
            _selectedArmies.Clear();
            foreach(var u in OwnArmies.Where(u=>I(u["x"])==at.X&&I(u["y"])==at.Y&&(ids==null||ids.Contains(I(u["id"])))))_selectedArmies.Add(I(u["id"]));
        }
        _map.SelectAndFocus(at);OpenHumanPage(army?"army-unit":"tile");RenderOutlinerRows();
    }
    private IEnumerable<JsonObject> OutlinerTiles=>HumanMode?(_map.Frame?["tiles"]?.AsArray()??new JsonArray())
        .Where(t=>S(t!["owner"])==HumanActor&&S(t["visibility"])=="live").Select(t=>t!.AsObject()):Enumerable.Empty<JsonObject>();
    private string HumanCellName(Vector2I p)
    {
        var tile=_map.Frame?["tiles"]?.AsArray().FirstOrDefault(t=>I(t!["position"]![0])==p.X&&I(t["position"]![1])==p.Y);
        return (S(tile?["name"]) is {Length:>0} name?name:S(tile?["terrain_name"]) is {Length:>0} terrain?terrain:"驻地")+$" ({p.X}, {p.Y})";
    }
    private void RenderHumanOutliner()
    {
        if(!HumanMode)return;
        _outlinerLand.Text=$"地块 {OutlinerTiles.Count()}";_outlinerArmy.Text=$"军队 {OwnArmies.Count()}";
        _outlinerLand.ToggleMode=_outlinerArmy.ToggleMode=true;
        _outlinerLand.ButtonPressed=!_outlinerArmies;_outlinerArmy.ButtonPressed=_outlinerArmies;
        _outlinerSearch.PlaceholderText=_outlinerArmies?"搜索驻地、兵种、编号":"搜索名称、坐标、建筑";
        ClearHumanChildren(_outlinerFilters);
        void Pick(string name,string caption,IEnumerable<(string Id,string Name)> choices,string value,Action<string> set)
        {
            var list=new[]{("",caption+"：全部")}.Concat(choices).ToArray();
            var pick=new OptionButton { Name=name,SizeFlagsHorizontal=SizeFlags.ExpandFill };
            foreach(var row in list)pick.AddItem(row.Item2);pick.Selected=Math.Max(0,Array.FindIndex(list,x=>x.Item1==value));
            _outlinerFilters.AddChild(pick);pick.AddThemeFontSizeOverride("font_size",14);
            pick.ItemSelected+=i=>{set(list[(int)i].Item1);RenderOutlinerRows();};
        }
        if(!_outlinerArmies)
        {
            Pick("OutlinerTerrain","地形",HumanFacts["terrain_catalog"]!.AsArray().Select(t=>(S(t!["id"]),HumanLabel(S(t["id"])))),_landTerrain,v=>_landTerrain=v);
            Pick("OutlinerResource","资源",OutlinerTiles.SelectMany(t=>t["resources"]!.AsArray()).Select(r=>S(r![0])).Distinct().Order().Select(id=>(id,HumanLabel(id))),_landResource,v=>_landResource=v);
            Pick("OutlinerBuilding","建筑",HumanFacts["building_catalog"]!.AsArray().Select(b=>(S(b!["id"]),HumanLabel(S(b["id"])))),_landBuilding,v=>_landBuilding=v);
            Pick("OutlinerLandState","状态",new[]{("construction","施工中"),("recruit","可征兵设施"),("garrison","有本国驻军")},_landState,v=>_landState=v);
        }
        else
        {
            Pick("OutlinerArmyKind","兵种",HumanFacts["unit_catalog"]!.AsObject().Select(p=>(p.Key,HumanLabel(p.Key))),_armyKind,v=>_armyKind=v);
            Pick("OutlinerArmyState","状态",new[]{("ready","可行动"),("engaged","交战中"),("new","新募"),("used","已行动")},_armyState,v=>_armyState=v);
        }
        var clear=Button(_outlinerFilters,"清除筛选",()=>
        {
            if(_outlinerArmies){_armyKind=_armyState=_armySearch="";}else{_landTerrain=_landResource=_landBuilding=_landState=_landSearch="";}
            _outlinerSearch.Text="";RenderHumanOutliner();
        });clear.Name="OutlinerClearFilters";HumanArt.Style(clear);
        RenderOutlinerRows();LayoutHumanOutliner();
    }
    private void RenderOutlinerRows()
    {
        if(!HumanMode||_outlinerList==null)return;
        ClearHumanChildren(_outlinerList);_outlinerRows.Clear();int displayed=0;
        if(!_outlinerArmies)
        {
            foreach(var tile in OutlinerTiles.OrderBy(t=>I(t["position"]![1])).ThenBy(t=>I(t["position"]![0])))
            {
                var p=new Vector2I(I(tile["position"]![0]),I(tile["position"]![1]));var buildings=tile["building_instances"]!.AsArray();
                string search=HumanCellName(p)+" "+tile["terrain_name"]+" "+string.Join(" ",buildings.Select(b=>HumanLabel(S(b!["kind"]))));
                if(!search.Contains(_landSearch,StringComparison.OrdinalIgnoreCase)||_landTerrain!=""&&S(tile["terrain"])!=_landTerrain||
                    _landResource!=""&&!tile["resources"]!.AsArray().Any(r=>S(r![0])==_landResource&&F(r[1])>0)||
                    _landBuilding!=""&&!buildings.Any(b=>S(b!["kind"])==_landBuilding)||
                    _landState=="construction"&&!buildings.Any(b=>I(b!["remaining"])>0)||_landState=="recruit"&&!HasRecruitFacility(tile)||
                    _landState=="garrison"&&!OwnArmies.Any(u=>I(u["x"])==p.X&&I(u["y"])==p.Y))continue;
                var icons=buildings.Select(b=>S(b!["kind"])).Distinct().Take(4).Select(k=>RtsArt.Building(k));
                AddOutlinerRow(p,HumanCellName(p),S(tile["terrain_name"])+$" · 建筑 {buildings.Count}"+(buildings.Any(b=>I(b!["remaining"])>0)?" · 施工中":""),icons,()=>SelectOutlinerCell(p,false));displayed++;
            }
            _outlinerCount.Text=$"本国地块 {displayed} / {OutlinerTiles.Count()}";
        }
        else
        {
            foreach(var group in OwnArmies.GroupBy(u=>(X:I(u["x"]),Y:I(u["y"]))).OrderBy(g=>g.Key.Y).ThenBy(g=>g.Key.X))
            {
                var p=new Vector2I(group.Key.X,group.Key.Y);var units=group.ToArray();
                var matching=units.Where(u=>(_armyKind==""||S(u["kind"])==_armyKind)&&(_armyState switch {
                    "ready"=>UnitReason(u)=="","engaged"=>Yes(u["engaged"]),"new"=>u["born_turn"]!=null&&I(u["born_turn"])==_period,
                    "used"=>u["moved_turn"]!=null&&I(u["moved_turn"])==_period,_=>true })&&
                    (HumanCellName(p)+" "+HumanLabel(S(u["kind"]))+" #"+I(u["id"])).Contains(_armySearch,StringComparison.OrdinalIgnoreCase)).ToArray();
                if(matching.Length==0)continue;
                string types=string.Join(" · ",matching.GroupBy(u=>S(u["kind"])).Select(g=>HumanLabel(g.Key)+"×"+g.Count()));
                string status=matching.Any(u=>Yes(u["engaged"]))?"交战中":matching.All(u=>u["born_turn"]!=null&&I(u["born_turn"])==_period)?"新募":matching.All(u=>UnitReason(u)!="")?"已行动":"可行动";
                AddOutlinerRow(p,HumanCellName(p),types+$"\n♥ {matching.Sum(u=>F(u["hp"])):G5} · {status}",
                    matching.Select(u=>S(u["kind"])).Distinct().Take(3).Select(k=>RtsArt.Unit(k,_map.NationColor(HumanActor))),()=>SelectOutlinerCell(p,true,matching.Select(u=>I(u["id"])).ToArray()));displayed++;
            }
            _outlinerCount.Text=$"本国军队 {OwnArmies.Count()} 支 · 筛选结果 {displayed} 处驻地";
        }
        if(displayed==0)_outlinerList.AddChild(new Label { Text=_outlinerArmies&&!OwnArmies.Any()?"暂无军队\n建造军屯或兵营后征召。":"没有符合条件的项目。",AutowrapMode=TextServer.AutowrapMode.WordSmart,Modulate=new Color("b7c4ad") });
    }
    private void AddOutlinerRow(Vector2I p,string title,string detail,IEnumerable<Texture2D> textures,Action action)
    {
        var button=new Button { Name=$"Outliner_{p.X}_{p.Y}",ToggleMode=true,ButtonPressed=_outlinerArmies?_selectionCell==p&&_selectedArmies.Count>0:_humanCell==p,CustomMinimumSize=new Vector2(0,94),SizeFlagsHorizontal=SizeFlags.ExpandFill,TooltipText=title+"\n"+detail+"\n点击定位并操作驻地" };
        HumanArt.Style(button);_outlinerList.AddChild(button);_outlinerRows[p]=button;button.Pressed+=action;
        var margin=new MarginContainer { MouseFilter=MouseFilterEnum.Ignore };margin.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);
        margin.OffsetLeft=6;margin.OffsetRight=-6;margin.OffsetTop=5;margin.OffsetBottom=-5;button.AddChild(margin);
        var box=new VBoxContainer { MouseFilter=MouseFilterEnum.Ignore };margin.AddChild(box);
        var name=new Label { Text=title,MouseFilter=MouseFilterEnum.Ignore,TextOverrunBehavior=TextServer.OverrunBehavior.TrimEllipsis };name.AddThemeFontSizeOverride("font_size",16);box.AddChild(name);
        var row=new HBoxContainer { MouseFilter=MouseFilterEnum.Ignore };box.AddChild(row);
        foreach(var texture in textures)row.AddChild(new TextureRect { Texture=texture,CustomMinimumSize=new Vector2(24,28),ExpandMode=TextureRect.ExpandModeEnum.IgnoreSize,StretchMode=TextureRect.StretchModeEnum.KeepAspectCentered,MouseFilter=MouseFilterEnum.Ignore });
        var info=new Label { Text=detail,MouseFilter=MouseFilterEnum.Ignore,SizeFlagsHorizontal=SizeFlags.ExpandFill,AutowrapMode=TextServer.AutowrapMode.WordSmart };info.AddThemeFontSizeOverride("font_size",14);row.AddChild(info);
    }
}
