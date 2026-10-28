using Godot;
using System;
using System.Collections.Generic;
using System.Linq;

public partial class MainMenu
{
    private bool _playerMode,_playerWizardStarted;
    private Control _playerSetup=null!,_editorBasics=null!,_editorScenario=null!,_editorExtraLabel=null!;
    private LineEdit _playerName=null!,_playerNickname=null!,_playerSeed=null!;
    private ColorPickerButton _playerColor=null!;
    private SpinBox _playerMapSize=null!;
    private OptionButton _playerScenario=null!,_playerIdentity=null!;
    private Label _playerWorldHint=null!,_editorTitle=null!;
    private Button _playerNext=null!,_editorBack=null!;
    private List<NationDraft>? _savedPlayerRoster;
    private int _savedPlayerScenario;
    public Button PlayerNextButton=>_playerNext;
    public Button PlayerBackButton=>_editorBack;
    public LineEdit PlayerNameField=>_playerName;
    public LineEdit PlayerSeedField=>_playerSeed;
    public ColorPickerButton PlayerColorField=>_playerColor;
    public SpinBox PlayerMapSizeField=>_playerMapSize;
    public void SelectPlayerIdentity(int index){_playerIdentity.Select(index);_playerIdentity.EmitSignal(OptionButton.SignalName.ItemSelected,(long)index);}

    private Control BuildPlayerSetup(Control parent)
    {
        var panel=Section(parent);
        var scroll=new ScrollContainer { SizeFlagsVertical=SizeFlags.ExpandFill,HorizontalScrollMode=ScrollContainer.ScrollMode.Disabled };panel.AddChild(scroll);
        var body=new VBoxContainer { SizeFlagsHorizontal=SizeFlags.ExpandFill };body.AddThemeConstantOverride("separation",18);scroll.AddChild(body);
        var title=new Label { Text="1 · 创建你的国家" };title.AddThemeFontSizeOverride("font_size",26);body.AddChild(title);
        BuildEditionControls(body);
        var own=new PanelContainer();own.AddThemeStyleboxOverride("panel",HumanArt.Box("panel"));body.AddChild(own);
        var row=new HBoxContainer();row.AddThemeConstantOverride("separation",20);own.AddChild(row);
        row.AddChild(new TextureRect { Texture=RtsArt.Building("native:town-hall"),CustomMinimumSize=new Vector2(104,120),
            ExpandMode=TextureRect.ExpandModeEnum.IgnoreSize,StretchMode=TextureRect.StretchModeEnum.KeepAspectCentered });
        var fields=new VBoxContainer { SizeFlagsHorizontal=SizeFlags.ExpandFill };fields.AddThemeConstantOverride("separation",10);row.AddChild(fields);
        var identity=new HBoxContainer();fields.AddChild(identity);identity.AddChild(FieldLabel("起始国家"));
        _playerIdentity=new OptionButton { SizeFlagsHorizontal=SizeFlags.ExpandFill };foreach(var p in Presets)_playerIdentity.AddItem(p.Name);identity.AddChild(_playerIdentity);
        var nameRow=new HBoxContainer();fields.AddChild(nameRow);nameRow.AddChild(FieldLabel("国家名称"));
        _playerName=new LineEdit { Text="秦",SizeFlagsHorizontal=SizeFlags.ExpandFill,MaxLength=20 };nameRow.AddChild(_playerName);
        var nickname=new HBoxContainer();fields.AddChild(nickname);nickname.AddChild(FieldLabel("玩家昵称"));
        _playerNickname=new LineEdit { Text="本地玩家",SizeFlagsHorizontal=SizeFlags.ExpandFill,MaxLength=32 };nickname.AddChild(_playerNickname);
        var colors=new HFlowContainer();colors.AddThemeConstantOverride("h_separation",7);fields.AddChild(colors);colors.AddChild(FieldLabel("国家颜色"));
        _playerColor=new ColorPickerButton { Color=new Color("9eb5cf"),EditAlpha=false,CustomMinimumSize=new Vector2(48,32) };colors.AddChild(_playerColor);
        foreach(var hex in Zhanguo.Desktop.Layout.DesktopScene.NationPalette)
        {
            var swatch=new Button { CustomMinimumSize=new Vector2(30,30),TooltipText="选用这个国家颜色",FocusMode=FocusModeEnum.None };
            var box=new StyleBoxFlat { BgColor=new Color(hex),BorderColor=new Color("d5cda9") };box.SetBorderWidthAll(1);
            swatch.AddThemeStyleboxOverride("normal",box);swatch.AddThemeStyleboxOverride("hover",box);swatch.Pressed+=()=>_playerColor.Color=new Color(hex);colors.AddChild(swatch);
        }
        _playerIdentity.ItemSelected+=i=>{_playerName.Text=Presets[(int)i].Name;_playerColor.Color=new Color(Zhanguo.Desktop.Layout.DesktopScene.NationPalette[(int)i%8]);};
        var worldTitle=new Label { Text="地图与开局" };worldTitle.AddThemeFontSizeOverride("font_size",22);body.AddChild(worldTitle);
        var world=new VBoxContainer();world.AddThemeConstantOverride("separation",12);body.AddChild(world);
        var scenarioRow=new HBoxContainer();scenarioRow.AddChild(FieldLabel("剧本"));world.AddChild(scenarioRow);
        _playerScenario=new OptionButton { SizeFlagsHorizontal=SizeFlags.ExpandFill };foreach(var s in _scenarioCatalog)_playerScenario.AddItem(s!["name"]!.ToString());scenarioRow.AddChild(_playerScenario);
        var settings=new HFlowContainer();settings.AddThemeConstantOverride("h_separation",18);settings.AddThemeConstantOverride("v_separation",12);world.AddChild(settings);
        var sizeRow=new HBoxContainer();sizeRow.AddChild(FieldLabel("地图边长"));settings.AddChild(sizeRow);
        _playerMapSize=new SpinBox { UpdateOnTextChanged=true,SelectAllOnFocus=true,MinValue=12,MaxValue=96,Value=24,Step=1,CustomMinimumSize=new Vector2(116,0) };sizeRow.AddChild(_playerMapSize);
        var seedRow=new HBoxContainer();seedRow.AddChild(FieldLabel("种子"));settings.AddChild(seedRow);
        _playerSeed=new LineEdit { Text="20261027",CustomMinimumSize=new Vector2(145,0) };seedRow.AddChild(_playerSeed);
        var random=new Button { Text="随机" };random.Pressed+=()=>_playerSeed.Text=Random.Shared.Next(1,2_000_000_000).ToString();seedRow.AddChild(random);
        _playerWorldHint=new Label { AutowrapMode=TextServer.AutowrapMode.WordSmart,Modulate=new Color("bac9b3") };world.AddChild(_playerWorldHint);
        _playerScenario.ItemSelected+=_=>RefreshPlayerWorld();
        var footer=new HBoxContainer();panel.AddChild(footer);
        _playerNext=new Button { Text="下一步：设置其他国家 →",CustomMinimumSize=new Vector2(0,48),SizeFlagsHorizontal=SizeFlags.ExpandFill };
        HumanArt.Style(_playerNext,"check");_playerNext.Pressed+=ContinuePlayerSetup;footer.AddChild(_playerNext);footer.AddChild(BackButton());
        _playerScenario.Select(1);RefreshPlayerWorld();return panel;
    }
    private void RefreshPlayerWorld()
    {
        var selected=_scenarioCatalog[_playerScenario.Selected]!;
        bool fixedMap=selected["fixed_roster"]!.GetValue<bool>();
        _playerMapSize.Editable=!fixedMap;
        if(fixedMap)_playerMapSize.Value=selected["map_size"]!.GetValue<int>();
        _playerWorldHint.Text=selected["description"]!.ToString()+(fixedMap?"\n本剧本锁定地图大小及八国身份；国名、颜色可自定义。":"\n你只控制一个国家，其余国家在下一步设置为 AI。");
    }
    public void SelectPlayerScenario(int index){_playerScenario.Select(index);RefreshPlayerWorld();}
    public void OpenPlayerGame()
    {
        if(!_playerWizardStarted)
        {
            SelectScenario(1);_playerIdentity.Select(0);_playerName.Text="秦";_playerSeed.Text=_seed.Text;
            _playerWizardStarted=true;
        }
        if(_savedPlayerRoster!=null)
        {
            _playerMode=true;
            SelectScenario(_savedPlayerScenario);RestoreNationEditor(_savedPlayerRoster);_savedPlayerRoster=null;
        }
        _playerMode=true;OpenPanel(_playerSetup);
    }
    private void ContinuePlayerSetup()
    {
        if(string.IsNullOrWhiteSpace(_playerName.Text)){_hint.Text="请填写自己的国家名称";return;}
        if(!int.TryParse(_playerSeed.Text.Trim(),out int seed)||seed<0){_hint.Text="地图种子须为非负整数";return;}
        if(_scenarioPicker.Selected!=_playerScenario.Selected)SelectScenario(_playerScenario.Selected);
        int preset=_playerIdentity.Selected;
        if(FixedScenario&&preset>=SelectedScenario["nations"]!.AsArray().Count){_hint.Text="该剧本只包含七雄与周王室，请选择对应起始国家";return;}
        var own=_nations.FirstOrDefault(n=>n.Client==3)??_nations[0];
        var clash=_nations.FirstOrDefault(n=>n!=own&&n.Preset==preset);
        if(clash!=null)
        {
            if(clash.Name==Presets[clash.Preset].Name)clash.Name=Presets[own.Preset].Name;
            clash.Preset=own.Preset;
        }
        own.Preset=preset;own.Name=_playerName.Text.Trim();own.Nickname=_playerNickname.Text.Trim();own.Color=_playerColor.Color.ToHtml(false);own.Client=3;
        var roster=new List<NationDraft> {own};roster.AddRange(_nations.Where(n=>n!=own));
        foreach(var opponent in roster.Skip(1))if(opponent.Client==3)opponent.Client=0;
        _seed.Text=seed.ToString();_mapSize.Value=_playerMapSize.Value;
        _playerMode=true;RestoreNationEditor(roster);SetEditorMode();OpenPanel(_newGame);
    }
    private void RestoreNationEditor(List<NationDraft> roster)
    {
        foreach(Node child in _nationList.GetChildren()){_nationList.RemoveChild(child);child.QueueFree();}
        _nations.Clear();_rows.Clear();_profilePickers.Clear();
        foreach(var nation in roster)
        {
            if(_playerMode&&nation.Client==3)_nations.Add(nation);
            else AddNationRow(nation);
        }
    }
    private void SetEditorMode()
    {
        _editorBasics.Visible=_editorScenario.Visible=_editorExtraLabel.Visible=_globalExtra.Visible=!_playerMode;
        var own=_nations.FirstOrDefault(n=>n.Client==3);
        _editorTitle.Text=_playerMode?$"2 · 设置其他国家\n你控制：{own?.Name}  ·  地图 {(int)_mapSize.Value} × {(int)_mapSize.Value}  ·  种子 {_seed.Text}":"看海模式 · 所有国家由 AI 控制";
        _editorBack.Text=_playerMode?"← 返回：本国与地图":"返回";
    }
}
