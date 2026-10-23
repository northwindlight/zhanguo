using Godot;
using System;
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
    private readonly Channel<string> _inbox = Channel.CreateBounded<string>(64);
    private WorldMap _map = null!;
    private Label _status = null!;
    private RichTextLabel _detail = null!;
    private RichTextLabel _events = null!;
    private RichTextLabel _market = null!;
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

    private MapDrawer _countries = null!, _details = null!, _shop = null!, _chronicle = null!;
    private Button _countriesButton = null!, _detailsButton = null!, _shopButton = null!, _chronicleButton = null!;
    private MapDrawer[] Drawers => new[] { _countries,_details,_shop,_chronicle };

    public override void _Ready()
    {
        GetTree().AutoAcceptQuit = false;
        var theme = new Theme { DefaultFontSize = 15 };
        var font = new SystemFont { FontNames = new[] { "Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC", "sans-serif" } };
        theme.DefaultFont = font;
        Theme = theme;
        foreach (string type in new[] { "Button", "PanelContainer", "TabContainer" })
        {
            var style = new StyleBoxFlat { BgColor = new Color("243332"), BorderColor = new Color("4d5c4c"),
                ContentMarginLeft = 12, ContentMarginRight = 12, ContentMarginTop = 8, ContentMarginBottom = 8 };
            style.SetBorderWidthAll(1);
            theme.SetStylebox(type=="Button"?"normal":"panel", type, style);
        }
        _map=new WorldMap();_map.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);AddChild(_map);
        _map.SelectionChanged=tile=>{ShowTile(tile);if(tile!=null)OpenDrawer(_details);};

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
        _countries.Body.AddChild(new Label { Text="离线八国 · v13\n独立演示局 · 上限300回合",Modulate=new Color("a4b09c") });
        _detail=TextBox(_details.Body);_detail.SizeFlagsVertical=SizeFlags.ExpandFill;
        _market=TextBox(_shop.Body);_market.SizeFlagsVertical=SizeFlags.ExpandFill;
        _events=TextBox(_chronicle.Body);_events.SizeFlagsVertical=SizeFlags.ExpandFill;_events.ScrollFollowing=true;

        var top=new HBoxContainer { MouseFilter=MouseFilterEnum.Ignore,ZIndex=5 };
        top.SetAnchorsAndOffsetsPreset(LayoutPreset.TopWide);top.OffsetLeft=16;top.OffsetRight=-16;top.OffsetTop=14;top.OffsetBottom=58;
        top.AddThemeConstantOverride("separation",12);AddChild(top);
        var title=new Label { Text="战国 · 观天下",MouseFilter=MouseFilterEnum.Ignore };
        title.AddThemeFontSizeOverride("font_size",24);title.AddThemeColorOverride("font_color",new Color("e4d8ae"));top.AddChild(title);
        top.AddChild(new Control { SizeFlagsHorizontal=SizeFlags.ExpandFill,MouseFilter=MouseFilterEnum.Ignore });
        _status=new Label { Text="正在创建离线八国世界…",MouseFilter=MouseFilterEnum.Ignore };top.AddChild(_status);
        _play=Button(top,"继续",()=>Send(_running?"pause":"resume"));
        _step=Button(top,"下一回合",()=>Send("step"));
        _save=Button(top,"保存",()=>Send("save"));
        var dock=new HBoxContainer { MouseFilter=MouseFilterEnum.Ignore,ZIndex=5 };
        dock.SetAnchorsAndOffsetsPreset(LayoutPreset.BottomLeft);dock.OffsetLeft=16;dock.OffsetTop=-58;dock.OffsetBottom=-14;
        dock.AddThemeConstantOverride("separation",8);AddChild(dock);
        _countriesButton=Button(dock,"列国",()=>ToggleDrawer(_countries));
        _detailsButton=Button(dock,"山河",()=>ToggleDrawer(_details));
        _shopButton=Button(dock,"市集",()=>ToggleDrawer(_shop));
        _chronicleButton=Button(dock,"纪事",()=>ToggleDrawer(_chronicle));
        var hint=new Label { Text="F 总览 · 空格 暂停/继续 · Esc 收起 · 滚轮缩放 / 右键拖动",MouseFilter=MouseFilterEnum.Ignore,ZIndex=5,
            HorizontalAlignment=HorizontalAlignment.Right,Modulate=new Color("c0c9b2") };
        hint.SetAnchorsAndOffsetsPreset(LayoutPreset.BottomRight);hint.OffsetLeft=-660;hint.OffsetRight=-18;hint.OffsetTop=-44;hint.OffsetBottom=-18;AddChild(hint);
        Resized+=LayoutDrawers;LayoutDrawers();
        var args=OS.GetCmdlineUserArgs();
        for(int i=0;i<args.Length-1;i++)if(args[i]=="--smoke-output")_smoke=args[i+1];
        try { StartHost(); } catch(Exception e) { Error(e.Message); }
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
        var text=new RichTextLabel {BbcodeEnabled=false,SelectionEnabled=true,SizeFlagsHorizontal=SizeFlags.ExpandFill};parent.AddChild(text);return text;
    }

    private void StartHost()
    {
        string root=OS.GetEnvironment("ZHANGUO_ROOT");
        if(root=="")root=Path.GetFullPath(Path.Combine(ProjectSettings.GlobalizePath("res://"),"../../.."));
        string python=OS.GetEnvironment("ZHANGUO_PYTHON");if(python=="")python=OS.GetName()=="Windows"?"python":"python3";
        string output=Path.Combine(root,"archive","desktop",DateTime.Now.ToString("yyyyMMdd-HHmmss")+"-"+Guid.NewGuid().ToString("N")[..6]);
        var info=new ProcessStartInfo(python) {WorkingDirectory=root,UseShellExecute=false,RedirectStandardInput=true,
            RedirectStandardOutput=true,RedirectStandardError=true,CreateNoWindow=true,StandardOutputEncoding=Encoding.UTF8,StandardErrorEncoding=Encoding.UTF8};
        info.Environment["PYTHONIOENCODING"]="utf-8";
        foreach(string arg in new[]{"-u","-m","ui.desktop","--output",output})info.ArgumentList.Add(arg);
        _host=Process.Start(info) ?? throw new Exception("无法启动 Python 宿主");
        _host.StandardInput.AutoFlush=true;
        _=ReadOutput();
        _=Task.Run(async()=>{while(await _host.StandardError.ReadLineAsync() is string line)GD.PrintErr(line);});
    }

    private async Task ReadOutput()
    {
        try
        {
            while(await _host!.StandardOutput.ReadLineAsync() is string line)await _inbox.Writer.WriteAsync(line);
            await _host.WaitForExitAsync();
            await _inbox.Writer.WriteAsync(JsonSerializer.Serialize(new {type="exit",code=_host.ExitCode}));
        }
        catch(Exception e){await _inbox.Writer.WriteAsync(JsonSerializer.Serialize(new {type="error",message=e.Message}));}
    }

    private void Send(string command)
    {
        if(_stopped||_host==null)return;
        try
        {
            _host.StandardInput.WriteLine(JsonSerializer.Serialize(new {command}));
            if(command is "pause" or "stop" or "save")_status.Text=command=="pause"?"暂停请求已排队 · 等待回合保存":command=="stop"?"正在完成回合并保存…":"等待回合边界保存…";
            _play.Disabled=true;_step.Disabled=true;
        }
        catch(Exception e){Error(e.Message);}
    }

    private void Error(string message)
    {
        _status.Text="宿主错误";_events.Text+="\n"+message;
        try { if(_host!=null&&!_host.HasExited)_host.StandardInput.WriteLine("{\"command\":\"pause\"}"); } catch(InvalidOperationException) { }
        _closing=false;
        _running=false;_play.Disabled=false;_step.Disabled=false;
        GD.PrintErr(message);
    }

    public override void _Process(double delta)
    {
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
        if(_smoke!=null && _elapsed>60){GD.PrintErr("DESKTOP_SMOKE_TIMEOUT");GetTree().Quit(2);}
    }

    private void Receive(JsonObject row)
    {
        string type=S(row["type"]);
        if(type=="exit")
        {
            _stopped=true;
            if(_closing && I(row["code"])==0)GetTree().Quit();
            else Error("Python 宿主退出，代码 "+row["code"]);
            return;
        }
        if(type=="error"){Error(S(row["message"]));_play.Disabled=false;return;}
        if(S(row["protocol"])!="zhanguo.desktop/1")throw new Exception("不支持的协议版本");
        string generation=S(row["generation"]);int revision=I(row["revision"]);
        if(_generation==null)_generation=generation;
        if(generation!=_generation||revision<=_revision)return;
        _revision=revision;
        if(type=="snapshot")
        {
            var frame=row["frame"]!.AsObject();_period=I(frame["period"]);_map.SetFrame(frame);
            UpdatePanels(frame);
            if(_smoke!=null && _period>=3 && !_capturing){_capturing=true;_=Capture();}
        }
        if(type=="status")
        {
            string state=S(row["state"]);_hostState=state;_running=state is "running" or "advancing";
            _savePath=S(row["save"]);
            string status=state switch {"running"=>"推演中","advancing"=>"列国行动中","paused"=>"已暂停","stopped"=>"已保存并停止",_=>state};
            _status.Text=$"第 {I(row["period"])} 回合 · {status} · 已存 {row["saved_period"]}";
            _status.TooltipText=_savePath;
            _play.Text=_running?"暂停":"继续";
            _play.Disabled=_closing;
            _step.Disabled=_closing||_running;
            _save.Disabled=_closing;
            if(_smoke!=null && !_smokeStarted && state=="paused") {_smokeStarted=true;Send("resume");}
        }
    }

    private void UpdatePanels(JsonObject frame)
    {
        foreach(Node child in _nations.GetChildren()){_nations.RemoveChild(child);child.QueueFree();}
        foreach(var n in frame["nations"]!.AsArray())
        {
            string id=S(n!["id"]);
            var button=Button(_nations,$"{n["name"]}    {n["land"]} 地",()=>{_selectedNation=id;_map.FocusNation(id);ShowTile(null);OpenDrawer(_details);});
            button.Modulate=_map.NationColor(id);
        }
        _market.Text=string.Join("\n",frame["market"]!.AsArray().Select(p=>$"{p!["name"],-6}   {F(p["price"]):0.00}"))+
            $"\n\n储蓄利率 {F(frame["bank"]!["rate"]):P1} · 贷款 {F(frame["bank"]!["loan_rate"]):P1}";
        _events.Text=string.Join("\n",frame["events"]!.AsArray().Select(e=>S(e!["text"])));
        ShowTile(_selectedNation!=""?null:_map.SelectedTile);
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
            _detail.Text=$"{tile["name"]}  ({p[0]}, {p[1]})\n{tile["terrain_name"]} · {(_map.Frame["names"]![owner]?.ToString()??"无主之地")}\n\n建筑\n"+
                (tile["building_labels"]!.AsArray().Count==0?"尚无建筑":string.Join("\n",tile["building_labels"]!.AsArray().Select(S)))+
                "\n\n军队\n"+(units.Length==0?"无已物化军队":string.Join("\n",units.Select(a=>$"{a!["label"]} #{a["id"]}  HP {a["hp"]}")))+
                "\n\n图标使用通用中世纪素材，\n建筑类型以此处文字为准。";
            return;
        }
        var nation=_map.Frame["nations"]!.AsArray().FirstOrDefault(n=>S(n!["id"])==_selectedNation);
        if(nation==null){_detail.Text="俯瞰山河，静观列国兴衰。\n\n点击地块查看地形、建筑和驻军。\n点击左侧国名查看国力。\n\n国界颜色表示当前归属；\n军旗数字表示本格真实军队数。\n无主守军按规则在遭遇时物化。\n\n本版采用 Mini World 像素素材。";return;}
        _detail.Text=$"{nation["name"]}国 · 第 {_period} 回合\n国土 {nation["land"]} 格\n总资产 {F(nation["assets"]):0.0}\n\n"+
            string.Join("\n",nation["stocks"]!.AsArray().Select(s=>$"{s!["name"]}    {s["amount"]}"))+"\n\n军队\n"+
            (nation["units"]!.AsObject().Count==0?"暂无军队":string.Join("  ",nation["units"]!.AsObject().Select(u=>$"{u.Key} {u.Value}")));
    }

    private async Task Screenshot(string path)
    {
        await ToSignal(RenderingServer.Singleton,RenderingServer.SignalName.FramePostDraw);
        using var image=GetViewport().GetTexture().GetImage();
        if(image.SavePng(path)!=Godot.Error.Ok)throw new IOException("截图保存失败");
    }
    private async Task PointerClick(Control button) => await PointerClickAt(button.GetGlobalRect().GetCenter());
    private async Task WaitFor(Func<bool> ready,string description)
    {
        var clock=Stopwatch.StartNew();
        while(!ready())
        {
            if(clock.Elapsed.TotalSeconds>30)throw new TimeoutException(description);
            await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
        }
    }
    private async Task PointerClickAt(Vector2 p)
    {
        Input.ParseInputEvent(new InputEventMouseButton { ButtonIndex=MouseButton.Left,Pressed=true,Position=p,GlobalPosition=p });
        await ToSignal(GetTree(),SceneTree.SignalName.ProcessFrame);
        Input.ParseInputEvent(new InputEventMouseButton { ButtonIndex=MouseButton.Left,Pressed=false,Position=p,GlobalPosition=p });
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
            int expected=_map.Frame["tiles"]!.AsArray().Sum(t=>t!["buildings"]!.AsArray().Count);
            if(_map.StructureCount!=expected || _map.TerrainBuilds!=1)throw new Exception("实时设施投影或地表缓存异常");
            File.WriteAllText(path+".json",JsonSerializer.Serialize(new { period=_period,tiles=_map.TileCount,armies=_map.ArmyCount,
                save=_savePath,artwork=_map.ArtworkMode,terrain_builds=_map.TerrainBuilds,static_draws=_map.ArtworkPasses,
                surfaces=_map.SurfaceCount,structures=_map.StructureCount,source_buildings=expected,
                map_fills_window=true,map_width=_map.Size.X,map_height=_map.Size.Y,viewport_width=Size.X,viewport_height=Size.Y,
                drawers_start_closed=true,drawer_pointer_checked=true,drawers_preserve_map_rect=true,drawers_within_viewport=true,drawer_close_checked=true,selection_checked=true,
                platform=OS.GetName(),renderer=RenderingServer.GetVideoAdapterName() },new JsonSerializerOptions{WriteIndented=true}));
            GD.Print($"DESKTOP_SMOKE_OK period={_period} tiles={_map.TileCount} armies={_map.ArmyCount}");
            _closing=true;Send("stop");
        }
        catch(Exception e){GD.PrintErr(e);_closing=true;Send("stop");GetTree().Quit(3);}
    }

    public override void _UnhandledKeyInput(InputEvent input)
    {
        if(input is not InputEventKey key||!key.Pressed||key.Echo)return;
        if(key.Keycode==Key.F){_map.Fit();CloseDrawers();}
        if(key.Keycode==Key.Escape)CloseDrawers();
        if(key.Keycode==Key.Space && !_closing)Send(_running?"pause":"resume");
    }
    public override void _Notification(int what)
    {
        if(what==NotificationWMCloseRequest)
        {
            if(_stopped||_host==null){GetTree().Quit();return;}
            if(_closing)return;
            _closing=true;Send("stop");
        }
    }
    public override void _ExitTree()
    {
        try{if(_host!=null&&!_host.HasExited)_host.StandardInput.Close();}catch(InvalidOperationException){}
        _host?.Dispose();
    }
}
