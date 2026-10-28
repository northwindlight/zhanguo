using Godot;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Channels;
using System.Threading.Tasks;
using static WorldMap;

public partial class Desktop : Control
{
    private Process? _host;
    private Task? _stderrPump;
    private readonly Channel<string> _inbox = Channel.CreateBounded<string>(64);
    private WorldMap _map = null!;
    private Label _status = null!;
    private RichTextLabel _detail = null!;
    private RichTextLabel _events = null!;
    private VBoxContainer _market = null!;
    private VBoxContainer _nations = null!;
    private Button _play = null!;
    private Button _step = null!;
    private Button _save = null!;
    private bool _running;
    private bool _closing;
    private bool _stopped;
    private string? _generation;
    private int _revision;
    private int _period;
    private string _selectedNation = "";
    private string? _smoke;
    private bool _capturing;
    private double _elapsed;
    private string _savePath = "";
    private string _hostState = "";
    private bool _smokeStarted;
    private Label _aiInfo=null!;
    private int _initialArmies=-1;
    private int _captureAfterPeriod=3;
    private bool _normalOpening;
    private Label _desktopTitle=null!;

    private MapDrawer _countries = null!, _details = null!, _shop = null!, _chronicle = null!;
    private Button _countriesButton = null!, _detailsButton = null!, _shopButton = null!, _chronicleButton = null!;
    private MapDrawer[] Drawers => _humanDrawer==null?new[] { _countries,_details,_shop,_chronicle }:new[] { _countries,_details,_shop,_chronicle,_humanDrawer };
    private SpeechBubbles _bubbles = null!;
    private readonly Dictionary<string,string> _models = new();
    private readonly Dictionary<string,string> _clientKinds = new();
    private string _liveActor="";
    private int _activitySeen;
    private MainMenu _menu = null!;
    private Zhanguo.Desktop.Layout.DesktopRuntime _runtime=null!;
    private bool _portableCheck;

    public override void _Ready()
    {
        GetTree().AutoAcceptQuit = false;
        var args=OS.GetCmdlineUserArgs();
        try
        {
            _runtime=Zhanguo.Desktop.Layout.DesktopRuntime.Resolve(OS.GetExecutablePath(),ProjectSettings.GlobalizePath("res://"),
                !OS.HasFeature("editor"),OS.GetEnvironment("ZHANGUO_ROOT"),OS.GetEnvironment("ZHANGUO_PYTHON"),OS.GetName()=="Windows");
        }
        catch(Exception e){GD.PrintErr(e.Message);GetTree().Quit(3);return;}
        for(int i=0;i<args.Length-1;i++)if(args[i]=="--portable-check")
        { _portableCheck=true;_=PortableCheck(args[i+1]);return; }
        var theme=PixelTheme.Create();
        Theme = theme;
        foreach (string type in new[] { "Button", "OptionButton", "LineEdit", "PanelContainer", "TabContainer" })
        {
            var style = new StyleBoxFlat { BgColor = new Color("243332"), BorderColor = new Color("4d5c4c"),
                ContentMarginLeft = 12, ContentMarginRight = 12, ContentMarginTop = 8, ContentMarginBottom = 8 };
            style.SetBorderWidthAll(2);
            style.SetCornerRadiusAll(0);
            bool field=type is "Button" or "OptionButton" or "LineEdit";
            theme.SetStylebox(field?"normal":"panel", type, style);
            if(field)
            {
                var hover=(StyleBoxFlat)style.Duplicate();hover.BgColor=new Color("354b46");hover.BorderColor=new Color("a5ac85");
                theme.SetStylebox("hover",type,hover);
                var pressed=(StyleBoxFlat)style.Duplicate();pressed.BgColor=new Color("162723");pressed.BorderColor=new Color("b6c597");
                theme.SetStylebox("pressed",type,pressed);
                var disabled=(StyleBoxFlat)style.Duplicate();disabled.BgColor=new Color("1c2927");
                theme.SetStylebox("disabled",type,disabled);
                theme.SetColor("font_color",type,new Color("e5e5d2"));
                theme.SetColor("font_disabled_color",type,new Color("78837b"));
            }
        }
        _map=new WorldMap();_map.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);AddChild(_map);
        _map.SelectionChanged=tile=>{if(HumanMode){HumanTileSelected(tile);return;}ShowTile(tile);if(tile!=null){if(S(tile["owner"])!="")RefreshNationLedger(S(tile["owner"]));OpenDrawer(_details);}};
        _bubbles=new SpeechBubbles();_bubbles.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);_bubbles.ZIndex=8;AddChild(_bubbles);

        _countries=Drawer("列国",MapDrawer.Side.Left);
        _details=Drawer("山河与国势",MapDrawer.Side.Right);
        _shop=Drawer("天下市集",MapDrawer.Side.Right);
        _chronicle=Drawer("天下纪事",MapDrawer.Side.Bottom);
        var scroller=new ScrollContainer { SizeFlagsVertical=SizeFlags.ExpandFill,HorizontalScrollMode=ScrollContainer.ScrollMode.Disabled };
        _countries.Body.AddChild(scroller);
        _nations=new VBoxContainer { SizeFlagsHorizontal=SizeFlags.ExpandFill };scroller.AddChild(_nations);
        Button(_countries.Body,"总览天下 · F",()=>{_selectedNation="";_map.Fit();ShowTile(null);CloseDrawers();});
        var borders=new CheckButton { Text="国界",ButtonPressed=true };_countries.Body.AddChild(borders);
        borders.Toggled+=v=>{_map.Borders=v;_map.QueueRedraw();};
        var units=new CheckButton { Text="军队",ButtonPressed=true };_countries.Body.AddChild(units);
        units.Toggled+=v=>{_map.Units=v;_map.QueueRedraw();};
        var guards=new CheckButton { Text="野人守军",ButtonPressed=true };_countries.Body.AddChild(guards);
        guards.Toggled+=v=>{_map.ShowGuards=v;_map.QueueRedraw();};
        var bubbleToggle=new CheckButton { Text="模型气泡",ButtonPressed=true };_countries.Body.AddChild(bubbleToggle);
        bubbleToggle.Toggled+=v=>_bubbles.Visible=v;
        _aiInfo=new Label { Text="正在核对实际客户端…",Modulate=new Color("a4b09c"),AutowrapMode=TextServer.AutowrapMode.WordSmart };_countries.Body.AddChild(_aiInfo);
        _detail=TextBox(_details.Body);_detail.SizeFlagsVertical=SizeFlags.ExpandFill;
        var marketScroll=new ScrollContainer { SizeFlagsVertical=SizeFlags.ExpandFill,HorizontalScrollMode=ScrollContainer.ScrollMode.Disabled };_shop.Body.AddChild(marketScroll);
        _market=new VBoxContainer { SizeFlagsHorizontal=SizeFlags.ExpandFill };_market.AddThemeConstantOverride("separation",10);marketScroll.AddChild(_market);
        _events=TextBox(_chronicle.Body);_events.SizeFlagsVertical=SizeFlags.ExpandFill;_events.ScrollFollowing=true;

        var top=new HBoxContainer { MouseFilter=MouseFilterEnum.Ignore,ZIndex=5 };
        top.SetAnchorsAndOffsetsPreset(LayoutPreset.TopWide);top.OffsetLeft=16;top.OffsetRight=-16;top.OffsetTop=14;top.OffsetBottom=58;
        top.AddThemeConstantOverride("separation",12);AddChild(top);
        var title=new Label { Text="战国 · 观天下",MouseFilter=MouseFilterEnum.Ignore };
        _desktopTitle=title;
        title.AddThemeFontSizeOverride("font_size",24);title.AddThemeColorOverride("font_color",new Color("e4d8ae"));top.AddChild(title);

        _status=new Label { Text="正在创建离线八国世界…",MouseFilter=MouseFilterEnum.Ignore,SizeFlagsHorizontal=SizeFlags.ExpandFill,TextOverrunBehavior=TextServer.OverrunBehavior.TrimEllipsis };top.AddChild(_status);
        BuildPlaybackControls(top);
        _play=Button(top,"继续",TogglePlayback);
        _step=Button(top,"下一回合",StepPlayback);
        _save=Button(top,"菜单 · Esc",OpenPauseMenu);
        _save.TooltipText="自动暂停演出，打开继续、保存与退出菜单";
        BuildPauseMenu();
        BuildNationLedger();
        var dock=new HBoxContainer { MouseFilter=MouseFilterEnum.Ignore,ZIndex=5 };
        _observerDock=dock;
        dock.SetAnchorsAndOffsetsPreset(LayoutPreset.BottomLeft);dock.OffsetLeft=16;dock.OffsetTop=-58;dock.OffsetBottom=-14;
        dock.AddThemeConstantOverride("separation",8);AddChild(dock);
        _countriesButton=Button(dock,"列国",()=>ToggleDrawer(_countries));
        _detailsButton=Button(dock,"山河",()=>ToggleDrawer(_details));
        _shopButton=Button(dock,"市集",()=>ToggleDrawer(_shop));
        _chronicleButton=Button(dock,"纪事",()=>ToggleDrawer(_chronicle));
        var hint=new Label { Text="F 总览 · 空格 暂停/继续 · Esc 菜单 · 滚轮缩放 / 右键拖动",MouseFilter=MouseFilterEnum.Ignore,ZIndex=5,
            HorizontalAlignment=HorizontalAlignment.Right,Modulate=new Color("c0c9b2") };
        hint.SetAnchorsAndOffsetsPreset(LayoutPreset.BottomRight);hint.OffsetLeft=-660;hint.AddThemeFontSizeOverride("font_size",14);hint.OffsetRight=-18;hint.OffsetTop=-44;hint.OffsetBottom=-18;AddChild(hint);
        _mapHint=hint;BuildHumanControls();
        Resized+=LayoutDrawers;LayoutDrawers();
        for(int i=0;i<args.Length-1;i++)if(args[i]=="--smoke-output"){_smoke=args[i+1];_playbackSpeed=96;}
        string? menuTest=null;
        for(int i=0;i<args.Length-1;i++)if(args[i]=="--menu-output")menuTest=args[i+1];
        for(int i=0;i<args.Length-1;i++)if(args[i]=="--ui-review-output"){_=ReviewDialogue(args[i+1]);return;}
        for(int i=0;i<args.Length-1;i++)if(args[i]=="--actor-review-output"){_=ReviewActors(args[i+1]);return;}
        for(int i=0;i<args.Length-1;i++)if(args[i]=="--font-review-output"){_=ReviewFonts(args[i+1]);return;}
        for(int i=0;i<args.Length-1;i++)if(args[i]=="--ledger-review-output"){_=ReviewLedger(args[i+1]);return;}
        for(int i=0;i<args.Length-1;i++)if(args[i]=="--effects-review-output"){_=ReviewEffects(args[i+1]);return;}
        for(int i=0;i<args.Length-1;i++)if(args[i]=="--human-review-output"){_=ReviewHuman(args[i+1]);return;}
        for(int i=0;i<args.Length-1;i++)if(args[i]=="--extinction-review-output"){_=ReviewExtinctionMemory(args[i+1]);return;}
        if(_smoke==null)
        {
            // Start screen: new game / load save / settings; smoke runs skip it.
            _menu=new MainMenu(_runtime.DataRoot,MenuBegin);
            _menu.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);_menu.ZIndex=30;AddChild(_menu);
            _status.Text="主菜单";
            if(menuTest!=null)_=MenuSelfTest(menuTest);
        }
        else
        {
            _status.Text="正在创建离线八国世界…";
            try { StartHost(null,null); } catch(Exception e) { Error(e.Message); }
        }
    }

    private void MenuBegin(string? config,string? resume)
    {
        _menu.Visible=false;
        _status.Text="正在创建世界…";
        try { StartHost(config,resume); }
        catch(Exception e) { _menu.Visible=true;_status.Text="主菜单";Error(e.Message); }
    }

    private async System.Threading.Tasks.Task MenuSelfTest(string path)
    {
        try
        {
            await ToSignal(RenderingServer.Singleton,RenderingServer.SignalName.FramePostDraw);
            await Screenshot(path);
            _menu.SeedField.Text="20261024";
            _menu.OpenNewGame();
            await ToSignal(GetTree().CreateTimer(.3),SceneTreeTimer.SignalName.Timeout);
            _menu.ValidateVisibleLayout();
            string stem=Path.ChangeExtension(path,null);
            await Screenshot(stem+"-newgame.png");
            var config=_menu.BuildConfig(out var error,out string? written);
            if(config==null||written==null)throw new Exception("菜单配置生成失败："+error);
            var json=JsonNode.Parse(await File.ReadAllTextAsync(written));
            if(json?["nations"]?.AsArray().Count!=8||json?["seed"]?.GetValue<int>()!=20261024
                ||json?["map_size"]?.GetValue<int>()!=24||json?["max_turns"]?.GetValue<int>()!=300||S(json?["scenario"])!="eight_nations")
                throw new Exception("菜单配置内容与表单不符");
            _menu.SelectScenario(1);
            var free=_menu.BuildConfig(out error,out written);
            if(free?["scenario"]!=null||free?["nations"]?.AsArray().Count!=2||free["nations"]!.AsArray().Any(n=>n!["position"]!=null))
                throw new Exception("自由开局沿用了八国剧本或固定落位");
            await Screenshot(stem+"-freegame.png");_menu.SelectScenario(0);
            _menu.OpenPlayerGame();_menu.SelectPlayerIdentity(6);
            await PointerClick(_menu.PlayerNextButton);
            var customPlayer=_menu.BuildConfig(out error,out written);
            if(customPlayer==null||customPlayer["nations"]!.AsArray().First(n=>S(n!["client"])=="human")!["name"]!.ToString()!="楚")
                throw new Exception("切换起始国家后名单冲突："+error);
            _menu.OpenNewGame();var watchAgain=_menu.BuildConfig(out error,out written);
            if(watchAgain==null||watchAgain["nations"]!.AsArray().Any(n=>S(n!["client"])=="human"))throw new Exception("看海模式残留真人席位："+error);
            _menu.OpenPlayerGame();await PointerClick(_menu.PlayerNextButton);
            if(_menu.BuildConfig(out error,out written)==null)throw new Exception("从看海返回丢失玩家配置："+error);
            _menu.OpenNewGame();
            _menu.ReviewLlmCard();
            await ToSignal(GetTree().CreateTimer(.3),SceneTreeTimer.SignalName.Timeout);
            _menu.ValidateVisibleLayout();
            await Screenshot(stem+"-llm.png");
            GetWindow().ContentScaleSize=new Vector2I(1024,768);GetWindow().Size=new Vector2I(1024,768);
            await ToSignal(GetTree().CreateTimer(.3),SceneTreeTimer.SignalName.Timeout);
            _menu.ValidateVisibleLayout();
            await Screenshot(stem+"-llm-1024.png");
            _menu.OpenSettings();
            await ToSignal(GetTree().CreateTimer(.3),SceneTreeTimer.SignalName.Timeout);
            _menu.ValidateVisibleLayout();
            await Screenshot(stem+"-settings.png");
            _menu.OpenLoad();
            await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
            await Screenshot(stem+"-load.png");
            GD.Print("MENU_SMOKE_OK "+path);
            GetTree().Quit();
        }
        catch(Exception e){GD.PrintErr(e);GetTree().Quit(3);}
    }

    private MapDrawer Drawer(string title,MapDrawer.Side side)
    {
        var drawer=new MapDrawer();drawer.Setup(title,side);AddChild(drawer);return drawer;
    }
    private void LayoutDrawers()
    {
        float left=Math.Min(290,Size.X-28),right=Math.Min(390,Size.X-28);
        float height=Math.Max(160,Size.Y-146),bottom=Math.Min(280,Size.Y*.42f);
        _countries.SetBounds(new Rect2(14,70,left,height));
        _details.SetBounds(new Rect2(Size.X-right-14,70,right,height));
        _shop.SetBounds(new Rect2(Size.X-right-14,70,right,height));
        _chronicle.SetBounds(new Rect2(14,Size.Y-bottom-76,Size.X-28,bottom));
        LayoutLedger();
        LayoutHuman();
    }
    private void CloseDrawers(bool animate=true) { foreach(var drawer in Drawers)drawer.SetOpen(false,animate); }
    private void OpenDrawer(MapDrawer drawer,bool animate=true)
    {
        foreach(var other in Drawers)other.SetOpen(other==drawer,animate);
    }
    private void ToggleDrawer(MapDrawer drawer)
    {
        if(drawer.IsOpen)drawer.SetOpen(false);else OpenDrawer(drawer);
    }

    private static Button Button(BoxContainer box,string text,Action action)
    {
        var b=new Button{Text=text,FocusMode=FocusModeEnum.None};b.Pressed+=action;box.AddChild(b);return b;
    }
    private static RichTextLabel TextBox(BoxContainer parent)
    {
        var text=new RichTextLabel {BbcodeEnabled=false,SelectionEnabled=true,AutowrapMode=TextServer.AutowrapMode.WordSmart,SizeFlagsHorizontal=SizeFlags.ExpandFill};parent.AddChild(text);return text;
    }

    private void StartHost(string? configPath=null,string? resumePath=null)
    {
        _selectedArmies.Clear();_replaceNextRoute=false;_armyRangeSuspended=false;_armyRangeDirty=true;
        string output=Path.Combine(_runtime.DataRoot,"archive","desktop",DateTime.Now.ToString("yyyyMMdd-HHmmss")+"-"+Guid.NewGuid().ToString("N")[..6]);
        var info=_runtime.Host(output,configPath,resumePath);
        if(_humanReviewDiagnostics)info.Environment["ZHANGUO_REVIEW_TRACE"]="1";
        _host=Process.Start(info) ?? throw new Exception("无法启动 Python 宿主");
        _host.StandardInput.AutoFlush=true;
        _stderrPump=Task.Run(async()=>{while(await _host.StandardError.ReadLineAsync() is string line)GD.PrintErr(line);});
        _=ReadOutput();
    }

    private async Task ReadOutput()
    {
        try
        {
            while(await _host!.StandardOutput.ReadLineAsync() is string line)await _inbox.Writer.WriteAsync(line);
            await _host.WaitForExitAsync();
            if(_stderrPump!=null)await _stderrPump;
            await _inbox.Writer.WriteAsync(JsonSerializer.Serialize(new {type="exit",code=_host.ExitCode}));
        }
        catch(Exception e){await _inbox.Writer.WriteAsync(JsonSerializer.Serialize(new {type="error",message=e.Message}));}
    }

    private void Send(string command)
    {
        if(_stopped||_host==null)return;
        if(_closing&&command!="stop")return;
        try
        {
            _host.StandardInput.WriteLine(JsonSerializer.Serialize(new {command}));
            if(command=="stop")
            {
                _playbackPaused=true;_beats.Clear();_playingBeat=null;_map.EndBeat();
                _save.Disabled=true;_host.StandardInput.Close();
            }
            if(command is "pause" or "stop" or "save")_status.Text=command=="pause"?"暂停请求已排队 · 等待回合保存":command=="stop"?"正在完成回合并保存…":"等待回合边界保存…";
            _play.Disabled=true;_step.Disabled=true;
        }
        catch(Exception e){Error(e.Message);}
    }

    private void Error(string message,string? actor=null)
    {
        // API/rule failures auto-pause: playback freezes, the banner explains,
        // and the failing nation keeps a chip at its territory.
        _playbackPaused=true;
        _status.Text="API 异常 · 已自动暂停";
        _events.Text+="\n"+message;
        if(actor is {Length:>0} who)
        {
            _map.ShowActivity(who,"模型异常 · 已暂停");
            _bannerTitle.Text=$"{_map.NationName(who)} · 模型异常";
        }
        else _bannerTitle.Text="宿主错误 · 已自动暂停";
        _bannerText.Text=message;
        try { if(_host!=null&&!_host.HasExited)_host.StandardInput.WriteLine("{\"command\":\"pause\"}"); } catch (InvalidOperationException) { }
        _closing=false;
        _running=false;_play.Disabled=false;_step.Disabled=false;
        GD.PrintErr(message);
    }

    public override void _Process(double delta)
    {
        if(_portableCheck)return;
        TickRts(delta);
        if(_pauseMenu.Visible)HideResourceTooltip();
        _elapsed+=delta;
        for(int i=0;i<16 && _inbox.Reader.TryRead(out var line);i++)
        {
            try{Receive(JsonNode.Parse(line)!.AsObject());}
            catch(Exception e)
            {
                Error("协议解析失败："+e.Message);
                if(_smoke!=null){_closing=true;Send("stop");GetTree().Quit(3);return;}
            }
        }
        ProcessPlayback(delta);
        if(HumanMode)_map.PlayerPanelOpen=_humanDrawer.IsOpen;
        if(HumanMode){LayoutHumanOutliner();DispatchHumanQueue();TickArmySelection();}
        _map.HudObstacles.Clear();
        foreach(var control in new Control[]{_ledgerPanel,_banner,_settlementCard,_outliner}.Concat(Drawers))
            if(control.IsVisibleInTree())
            {
                var rect=control.GetGlobalRect();var at=rect.Position-_map.GlobalPosition;
                _map.HudObstacles.Add(new(at.X,at.Y,rect.Size.X,rect.Size.Y));
            }
        if(_smoke!=null && _elapsed>180){GD.PrintErr("DESKTOP_SMOKE_TIMEOUT");GetTree().Quit(2);}
    }

    private void Receive(JsonObject row)
    {
        string type=S(row["type"]);
        if(type=="exit")
        {
            if(_smoke!=null)GD.Print("DESKTOP_HOST_EXIT "+row["code"]);
            _stopped=true;
            if(_closing && I(row["code"])==0)GetTree().Quit();
            else Error("Python 宿主退出，代码 "+row["code"]);
            return;
        }
        if(type=="error"){Error(S(row["message"]),row["actor"]?.ToString());_play.Disabled=false;return;}
        if(S(row["protocol"])!="zhanguo.desktop/1")throw new Exception("不支持的协议版本");
        string generation=S(row["generation"]);int revision=I(row["revision"]);
        if(_generation==null)_generation=generation;
        if(generation!=_generation||revision<=_revision)return;
        _revision=revision;
        if(ReceiveHuman(row))return;
        if(_closing&&(type is "timeline" or "snapshot"))return;
        if(type=="stream")
        {
            _liveActor=S(row["actor"]);RefreshSpeedDisplay();
            if(_ledgerActor!=_liveActor)RefreshNationLedger(_liveActor);
            UpdateStreamSpeaker(S(row["actor"]));
            _bubbles.HandleStream(row);
            return;
        }
        if(type=="activity")
        {
            _activitySeen++;
            string actor=S(row["actor"]),text=S(row["text"]);
            _liveActor=actor;RefreshSpeedDisplay();
            if(_ledgerActor!=actor)RefreshNationLedger(actor);
            _map.ShowActivity(actor,text);
            if(!HasTimeline)
            {
                _bannerTitle.Text=$"{_map.NationName(actor)} · 第 {_period+1} 回合";
                _bannerText.Text=text;
            }
            if(!HasTimeline)_status.Text=$"第 {_period+1} 回合 · {_map.NationName(actor)}{text}";
            return;
        }
        if(type=="notice")
        {
            _bannerTitle.Text="演出同步";_bannerText.Text=S(row["message"]);GD.PrintErr(_bannerText.Text);return;
        }
        if(type=="timeline"){ReceiveTimeline(row);return;}
        if(type=="snapshot")
        {
            var frame=row["frame"]!.AsObject();
            if(HasTimeline){_pendingFrame=frame;if(_awaitingFrame)FinishPlayback();}
            else ApplyFrame(frame);
        }
        if(type=="status")
        {
            string state=S(row["state"]);_hostState=state;
            _autoRunning=row["auto_running"]?.GetValue<bool>()==true;
            _running=state is "running" or "advancing" or "presenting" ||
                row["cycle_active"]?.GetValue<bool>()==true && row["playback_paused"]?.GetValue<bool>()!=true && state!="stopping";
            if(_smoke!=null&&state=="stopped")GD.Print("DESKTOP_HOST_STOPPED");
            _savePath=S(row["save"]);
            string status=state switch {"running"=>"推演中","advancing"=>"列国行动中","presenting"=>"展示本次调用",
                "playback-paused"=>"本批演出已暂停","save-queued"=>"保存已排队，等待完整回合",
                "stopping"=>"正在完成回合并保存","paused"=>"已暂停","stopped"=>"已保存并停止",_=>state};
            _status.Text=$"第 {I(row["period"])} 回合 · {status} · 已存 {row["saved_period"]}";
            _status.TooltipText=_savePath;
            _play.Text=_running?"暂停":"继续";
            _play.Disabled=_closing;
            _step.Disabled=_closing||_running;
            _save.Disabled=_closing;
            if(_smoke!=null && !_smokeStarted && state=="paused") {_smokeStarted=true;_playbackPaused=false;Send("resume");}
            if(state=="paused")_map.ClearActivity();
            RefreshPauseMenu(state);
            RefreshPlaybackControls();
            if(HumanMode)
            {
                _humanCycleActive=row["cycle_active"]?.GetValue<bool>()==true;
                _humanPaused=row["playback_paused"]?.GetValue<bool>()==true;
                _savedHumanPeriod=row["saved_period"]==null?-1:I(row["saved_period"]);
                // This status can acknowledge the preceding batch after the
                // next queued write was sent. Only its own result/frame may
                // release _humanBusy; a generic ready status cannot do so.
                if(state=="human-waiting")_humanPaused=false;
                _status.Text=$"第 {I(row["period"])+1} 回合 · {(state=="human-waiting"?"轮到你了":state=="advancing"?"列国行动中":status)} · 已存 {_savedHumanPeriod} 期";
                if(state=="exit-choice")ShowHumanExitChoice();
                if(state=="discarded"&&_closing){_stopped=true;GetTree().Quit();}
                RefreshHumanButtons();
            }
        }
    }

    private void UpdateStreamSpeaker(string actor)
    {
        if(actor==""||_map.Frame==null)return;
        var names=_map.Frame["names"]!.AsObject();
        if(!names.ContainsKey(actor))return;
        _bubbles.SetSpeaker(actor,names[actor]!.ToString(),_models.GetValueOrDefault(actor,""),_map.NationColor(actor));
    }

    private void UpdatePanels(JsonObject frame)
    {
        foreach(Node child in _nations.GetChildren()){_nations.RemoveChild(child);child.QueueFree();}
        foreach(var n in frame["nations"]!.AsArray())
        {
            string id=S(n!["id"]);
            var button=Button(_nations,$"{n["name"]}    {n["land"]} 地",()=>{_selectedNation=id;_map.FocusNation(id);RefreshNationLedger(id);ShowTile(null);OpenDrawer(_details);});
            button.Modulate=_map.NationColor(id);
        }
        RenderMarketOverview(frame);
        _events.Text=string.Join("\n",frame["events"]!.AsArray().Select(e=>S(e!["text"])));
        ShowTile(_selectedNation!=""?null:_map.SelectedTile);
        RefreshNationLedger();
        _models.Clear();
        _clientKinds.Clear();
        if(frame["clients"] is JsonArray clients)
        {
            string versions=string.Join(" / ",clients.Select(c=>S(c!["version"])).Distinct());
            _aiInfo.Text=clients.Any(c=>S(c!["kind"])=="llm")?"模型对局 · 实时思考与发言":$"规则对局 · {versions}";
            _aiInfo.TooltipText=string.Join("\n",clients.Select(c=>$"{frame["names"]![S(c!["actor"])]}: {c["version"]} · {c["strategy"]}"));
            foreach(var c in clients)
            {
                string actor=S(c!["actor"]),kind=S(c["kind"]);
                _models[actor]=kind=="llm"?S(c["model"]):S(c["version"]);_clientKinds[actor]=kind;
            }
        }
    }

    private void ShowTile(JsonObject? tile)
    {
        if(_map.Frame==null)return;
        if(tile!=null)
        {
            _selectedNation="";
            var p=tile["position"]!;string owner=S(tile["owner"]);
            if(tile["passable"]?.GetValue<bool>()==false)
            {
                _detail.Text=$"{tile["terrain_name"]}  ({p[0]}, {p[1]})\n\n自然屏障\n不可通行、占领或建造\n无资源与野人守军\n\n请沿可通行陆地寻找绕行路线。";
                return;
            }
            var units=_map.Frame["armies"]!.AsArray().Where(a=>I(a!["x"])==I(p[0])&&I(a["y"])==I(p[1])).ToArray();
            bool potential=_map.Frame["scene"]?["guards"]?.AsArray().Any(u=>I(u!["x"])==I(p[0])&&I(u["y"])==I(p[1]))==true;
            _detail.Text=$"{tile["name"]}  ({p[0]}, {p[1]})\n{tile["terrain_name"]} · {(_map.Frame["names"]![owner]?.ToString()??"无主之地")}\n\n建筑\n"+
                (tile["building_labels"]!.AsArray().Count==0?"尚无建筑":string.Join("\n",tile["building_labels"]!.AsArray().Select(S)))+
                "\n\n军队\n"+(potential?"野人守军 · 1支 · 未遭遇\n":"")+(units.Length==0?(potential?"":"无驻军"):string.Join("\n",units.Select(a=>$"{a!["label"]??a["kind"]} #{a["id"]}  HP {a["hp"]}")))+
                "\n\n生产设施按实际清单显示；市政厅旁集市用于展示买卖。";
            return;
        }
        var nation=_map.Frame["nations"]!.AsArray().FirstOrDefault(n=>S(n!["id"])==_selectedNation);
        if(nation==null){_detail.Text="俯瞰山河，静观列国兴衰。\n\n点击地块查看地形、建筑和驻军。\n点击左侧国名查看国力。\n\n国界颜色表示当前归属；\n士兵脚下标注真实兵种数量，上方为HP和地块减伤。\n无主守军按规则在遭遇时物化。\n\n市政厅旁的集市展示交易，生产设施以清单为准。";return;}
        var client=_map.Frame["clients"]?.AsArray().FirstOrDefault(c=>S(c!["actor"])==_selectedNation);
        _detail.Text=$"{nation["name"]}国 · 第 {_period+1} 回合\n{client?["version"]} · {client?["strategy"]}\n国土 {nation["land"]} 格\n上期结算资产 {F(nation["assets"]):0.0}\n\n"+
            string.Join("\n",nation["stocks"]!.AsArray().Select(s=>$"{s!["name"]}    {s["amount"]}"))+"\n\n军队\n"+
            (nation["units"]!.AsObject().Count==0?"暂无军队":string.Join("  ",nation["units"]!.AsObject().Select(u=>$"{u.Key} {u.Value}")));
    }

    private async Task Screenshot(string path)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path))!);
        await ToSignal(RenderingServer.Singleton,RenderingServer.SignalName.FramePostDraw);
        using var image=GetViewport().GetTexture().GetImage();
        if(image.SavePng(path)!=Godot.Error.Ok)throw new IOException("截图保存失败");
    }
    private async Task PointerClick(Control button)
    {
        await WaitFor(()=>Drawers.All(d=>d.IsSettled),"点击前抽屉尚未展开完成");
        await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
        for(Node? parent=button.GetParent();parent!=null;parent=parent.GetParent())
            if(parent is ScrollContainer scroll)scroll.EnsureControlVisible(button);
        await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
        var at=button.GetGlobalRect().GetCenter();Viewport viewport=button.GetViewport();
        while(viewport is Window window&&window!=GetWindow()&&window.IsEmbedded())
        {at+=window.Position;viewport=window.GetParent().GetViewport();}
        await PointerClickAt(at,viewport);
    }
    private async Task WaitFor(Func<bool> ready,string description,double timeout=30)
    {
        var clock=Stopwatch.StartNew();
        while(!ready())
        {
            if(clock.Elapsed.TotalSeconds>timeout)throw new TimeoutException(description+"；诊断 "+JsonSerializer.Serialize(new {
                period=_period,revision=_revision,host=_hostState,running=_running,turnPeriod=_humanTurn?["period"]?.GetValue<int>(),
                needsFrame=_humanNeedsFrame,busy=_humanBusy,ended=_humanEnded,paused=_humanPaused,
                timeline=HasTimeline,sequence=_timelineSequence,stage=_timelineStage,phase=S(_playingBeat?["phase"]),
                beat=_beatNumber,total=_beatTotal,awaitingFrame=_awaitingFrame,pendingFrame=_pendingFrame!=null,
                playbackPaused=_playbackPaused,elapsed=_beatElapsed,queries=_humanQueries.Count }));
            await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
        }
    }
    private async Task PointerClickAt(Vector2 p,Viewport? target=null)
    {
        var viewport=target??GetViewport();
        viewport.PushInput(new InputEventMouseButton { ButtonIndex=MouseButton.Left,Pressed=true,Position=p,GlobalPosition=p },true);
        await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
        viewport.PushInput(new InputEventMouseButton { ButtonIndex=MouseButton.Left,Pressed=false,Position=p,GlobalPosition=p },true);
        await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
        await WaitFor(()=>Drawers.All(d=>d.IsSettled),"抽屉动画未完成");
    }
    private async Task Capture()
    {
        try
        {
            Send("pause");
            await WaitFor(()=>_hostState=="paused","暂停未在完整回合边界完成");
            if(Drawers.Any(d=>d.IsOpen))throw new Exception("抽屉默认未收起");
            if(_map.Position!=Vector2.Zero || _map.Size.DistanceTo(Size)>.5f)throw new Exception("地图未铺满窗口");
            string path=_smoke!,stem=Path.ChangeExtension(path,null);
            _map.Fit();await Screenshot(path);
            Vector2 mapSize=_map.Size;float zoom=_map.Zoom;
            foreach(var (button,drawer,suffix) in new[] {
                (_countriesButton,_countries,"nations"),(_shopButton,_shop,"market"),(_chronicleButton,_chronicle,"events") })
            {
                await PointerClick(button);
                if(!drawer.IsOpen || Drawers.Count(d=>d.IsOpen)!=1)throw new Exception("抽屉按钮未响应真实指针事件："+suffix);
                if(_map.Size!=mapSize || _map.Zoom!=zoom)throw new Exception("抽屉挤压或缩放了地图");
                if(!new Rect2(Vector2.Zero,Size).Encloses(drawer.GetGlobalRect()))throw new Exception("抽屉超出窗口："+suffix);
                await Screenshot(stem+"-"+suffix+".png");
            }
            CloseDrawers(false);
            var first=_map.Frame!["tiles"]!.AsArray().First(t=>S(t!["owner"])=="scenario:qin" &&
                t["buildings"]!.AsArray().Any(b=>S(b![0])=="native:town-hall"))!;
            _map.FocusNation("scenario:qin");
            var cell=new Vector2I(I(first["position"]![0]),I(first["position"]![1]));
            var click=_map.CellCenter(cell);
            if(!new Rect2(Vector2.Zero,Size).HasPoint(click))throw new Exception("聚焦后的首都不在视口内");
            await PointerClickAt(click);
            if(_map.Selected!=cell || !_detail.Text.Contains(S(first["name"])) || !_details.IsOpen)
                throw new Exception("地块点选或详情抽屉失败");
            if(!new Rect2(Vector2.Zero,Size).Encloses(_details.GetGlobalRect()))throw new Exception("详情抽屉超出窗口");
            await Screenshot(stem+"-detail.png");
            await PointerClick(_details.CloseButton);
            if(Drawers.Any(d=>d.IsOpen))throw new Exception("抽屉收起按钮失效");
            await Screenshot(stem+"-close.png");
            // Render check for the live activity chip with synthetic content;
            // real activity messages are counted separately below.
            _map.ShowActivity("scenario:qin","正在写信（渲染检查）");
            await Screenshot(stem+"-activity.png");
            _map.ClearActivity();
            if(_activitySeen==0)throw new Exception("宿主活动消息（思考/处理政务等）未到达");
            int expected=_map.Frame["tiles"]!.AsArray().Sum(t=>t!["buildings"]!.AsArray().Count);
            if(_map.StructureCount!=expected || _map.TerrainBuilds!=1)throw new Exception("实时设施投影或地表缓存异常");
            if((_normalOpening&&_initialArmies!=0)||_map.Residents==0||_map.Guards==0||_playedPhases.GetValueOrDefault("actor")<8||_playedPhases.GetValueOrDefault("build")==0||_nationalSettlements<8||_map.RegionNodes!=_map.TileCount)
                throw new Exception("正常开局、居民野人、逐国动作或地区绘制缓存验收失败");
            ValidateLedgerLayout();
            VerifyUnifiedArt();
            if(_playedPhases.GetValueOrDefault("production")!=_settlementBatches*8)
                throw new Exception("正常八国的生产阶段没有按国汇总");
            if(_normalOpening&&_bubbles.SegmentsShown<_playedPhases.GetValueOrDefault("actor"))
                throw new Exception("模型气泡未跟随逐国演出");
            if(!_bubbles.EverShown)
                throw new Exception("模型气泡从未实际显示（渲染可见性验收失败）");
            File.WriteAllText(path+".json",JsonSerializer.Serialize(new { period=_period,tiles=_map.TileCount,armies=_map.ArmyCount,
                save=_savePath,artwork=_map.ArtworkMode,terrain_builds=_map.TerrainBuilds,static_draws=_map.ArtworkPasses,
                surfaces=_map.SurfaceCount,structures=_map.StructureCount,source_buildings=expected,
                initial_armies=_initialArmies,residents=_map.Residents,potential_guards=_map.Guards,region_nodes=_map.RegionNodes,
                bubble_segments=_bubbles.SegmentsShown,readable_font=Theme?.DefaultFont is FontFile,pixel_font=PixelTheme.FontPath,
                activity_events=_activitySeen,
                action_batches=_actionBatches,settlement_batches=_settlementBatches,pause_menu_checked=_pauseMenuChecked,
                action_marches=_actionMarches,settlement_marches=_settlementMarches,
                national_settlements=_nationalSettlements,ledger_actor=_ledgerActor,ledger_switches=_ledgerSwitches,
                markets=_map.MarketCount,road_tiles=_map.RoadTiles,national_cloth_checked=true,
                ledger_within_viewport=true,ledger_rows=_ledgerStocks.GetChildCount()/3,
                played_phases=_playedPhases,actor_banners=_actorBanners,political_alpha_near=_map.PoliticalOpacity,
                map_fills_window=true,map_width=_map.Size.X,map_height=_map.Size.Y,viewport_width=Size.X,viewport_height=Size.Y,
                drawers_start_closed=true,drawer_pointer_checked=true,drawers_preserve_map_rect=true,drawers_within_viewport=true,drawer_close_checked=true,selection_checked=true,
                platform=OS.GetName(),renderer=RenderingServer.GetVideoAdapterName() },new JsonSerializerOptions{WriteIndented=true}));
            GD.Print($"DESKTOP_SMOKE_OK period={_period} tiles={_map.TileCount} armies={_map.ArmyCount}");
            _closing=true;Send("stop");
        }
        catch(Exception e){GD.PrintErr(e);_closing=true;Send("stop");GetTree().Quit(3);}
    }

    public override void _Input(InputEvent input)
    {
        if(input is InputEventKey control&&control.Keycode==Key.Ctrl){_armyCtrlHeld=control.Pressed;_armyRangeDirty=true;_manualArmyTarget=false;}
        if(input is InputEventKey {Pressed:true,Echo:false,Keycode:Key.Escape} && _host!=null && !_closing)
        {
            if(HumanMode&&(_humanTargetMode!=""||_replaceNextRoute)){_replaceNextRoute=false;CancelHumanTarget(true);GetViewport().SetInputAsHandled();return;}
            if(_pauseMenu.Visible)ClosePauseMenu();else OpenPauseMenu();
            GetViewport().SetInputAsHandled();
        }
    }
    public override void _UnhandledKeyInput(InputEvent input)
    {
        if(input is not InputEventKey key||!key.Pressed||key.Echo)return;
        if(_pauseMenu.Visible)return;
        if(key.Keycode==Key.F){_map.Fit();CloseDrawers();}
        if(key.Keycode==Key.Space && !_closing)TogglePlayback();
    }
    public override void _Notification(int what)
    {
        if(what==NotificationWMCloseRequest)
        {
            QuitGame();
        }
    }
    public override void _ExitTree()
    {
        try{if(_host!=null&&!_host.HasExited)_host.StandardInput.Close();}catch(InvalidOperationException){}
        _host?.Dispose();
    }
}
