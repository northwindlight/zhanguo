using Godot;
using System;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;

public partial class Desktop
{
    private Control _pauseMenu=null!;
    private PanelContainer _pauseCard=null!;
    private Label _pauseInfo=null!;
    private Button _menuContinue=null!,_menuSave=null!,_menuQuit=null!;
    private Label _gameRulesLabel=null!;
    private bool _resumeAfterMenu,_autoRunning,_menuAutoRunning;
    private bool _pauseMenuChecked,_pauseMenuReviewStarted;

    private void BuildPauseMenu()
    {
        _pauseMenu=new Control { Visible=false,ZIndex=40,MouseFilter=MouseFilterEnum.Stop };
        _pauseMenu.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);AddChild(_pauseMenu);
        var shade=new ColorRect { Color=new Color(0,0,0,.65f),MouseFilter=MouseFilterEnum.Stop };
        shade.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);_pauseMenu.AddChild(shade);
        var center=new CenterContainer { MouseFilter=MouseFilterEnum.Ignore };
        center.SetAnchorsAndOffsetsPreset(LayoutPreset.FullRect);_pauseMenu.AddChild(center);
        _pauseCard=new PanelContainer { CustomMinimumSize=new Vector2(400,0) };center.AddChild(_pauseCard);
        var style=new StyleBoxFlat { BgColor=new Color("1c2c29"),BorderColor=new Color("a5ac85"),
            ContentMarginLeft=28,ContentMarginRight=28,ContentMarginTop=24,ContentMarginBottom=24 };
        style.SetBorderWidthAll(2);_pauseCard.AddThemeStyleboxOverride("panel",style);
        var box=new VBoxContainer();box.AddThemeConstantOverride("separation",16);_pauseCard.AddChild(box);
        var title=new Label { Text="游戏菜单",HorizontalAlignment=HorizontalAlignment.Center };
        title.AddThemeFontSizeOverride("font_size",28);box.AddChild(title);
        _gameRulesLabel=new Label { Name="GameDistribution",Visible=false,AutowrapMode=TextServer.AutowrapMode.WordSmart,MaxLinesVisible=3 };
        _gameRulesLabel.AddThemeFontSizeOverride("font_size",14);box.AddChild(_gameRulesLabel);
        _pauseInfo=new Label { AutowrapMode=TextServer.AutowrapMode.WordSmart,CustomMinimumSize=new Vector2(344,80),
            HorizontalAlignment=HorizontalAlignment.Center };box.AddChild(_pauseInfo);
        _menuContinue=Button(box,"继续观看 · Esc",ClosePauseMenu);
        _menuSave=Button(box,"保存",()=>{Send("save");_pauseInfo.Text="保存请求已提交；未完成的回合会在继续并结束回合后保存。";});
        BuildDisplayControls(box);
        _menuQuit=Button(box,"保存并退出游戏",QuitGame);
    }

    private void ApplyDistribution(System.Text.Json.Nodes.JsonNode? info)
    {
        if(_gameRulesLabel==null)return;
        _gameRulesLabel.Visible=info!=null;if(info==null)return;
        string features=string.Join(" · ",info["features"]!.AsObject().Select(p=>(p.Value!["short_name"]??p.Value["name"])+(p.Value["enabled"]!.GetValue<bool>()?" 开":" 关")));
        _gameRulesLabel.Text=$"本局规则：{info["name"]} {info["version"]}\n"+(features==""?"无实验服务":features);_gameRulesLabel.TooltipText=_gameRulesLabel.Text;
    }

    private void OpenPauseMenu()
    {
        if(_closing||_host==null||_pauseMenu.Visible)return;
        _resumeAfterMenu=!_playbackPaused&&(HasTimeline||_running);
        _menuAutoRunning=_autoRunning;
        _playbackPaused=true;_pauseMenu.Visible=true;CloseDrawers();
        _pauseInfo.Text=HumanMode?"游戏已暂停，继续后可操作或观看本批演出。\n保存以完整回合为边界；回合中保存会排队。":"演出已暂停。当前模型调用结束后，宿主停在本批操作处。\n存档仍以完整回合为边界。";
        Send("pause");RefreshPlaybackControls();
    }

    private void ClosePauseMenu()
    {
        if(!_pauseMenu.Visible||_closing)return;
        _pauseMenu.Visible=false;
        if(HumanMode){_humanDiscard.Visible=false;_menuQuit.Visible=true;}
        if(_resumeAfterMenu||HumanMode)
        {
            _playbackPaused=false;
            try {_host!.StandardInput.WriteLine(JsonSerializer.Serialize(new { command="resume",cycle_only=!_menuAutoRunning }));}
            catch(Exception e){Error(e.Message);}
            _autoRunning=_menuAutoRunning;
        }
        RefreshPlaybackControls();
    }

    private void RefreshPauseMenu(string state)
    {
        if(!_pauseMenu.Visible)return;
        if(HumanMode&&_humanDiscard.Visible){_pauseInfo.Text=HumanExitInfo;_pauseInfo.TooltipText=_savePath;return;}
        _pauseInfo.Text=state switch {
            "save-queued"=>"保存已排队。继续观看至本回合结束后写入，当前保留上一完整回合的存档。",
            "playback-paused"=>"本批操作已暂停。继续观看后再发起后续调用。\n保存以完整回合为边界。",
            "paused"=>"已在完整回合暂停并保存。\n按 Esc 返回观看画面。",
            "stopping"=>"正在完成当前回合并保存，完成后自动退出。",
            "stopped"=>"已保存，正在退出。",
            _=>_pauseInfo.Text };
        _pauseInfo.TooltipText=_savePath;
    }

    private void QuitGame()
    {
        if(_stopped||_host==null){GetTree().Quit();return;}
        if(_closing)return;
        if(HumanMode&&(_humanCycleActive||HasTimeline||_humanTurn!=null)){ShowHumanExitChoice();return;}
        _closing=true;_playbackPaused=true;
        _menuContinue.Disabled=true;_menuSave.Disabled=true;_menuQuit.Disabled=true;
        _pauseInfo.Text="正在完成当前回合并保存，完成后自动退出。";
        Send("stop");
    }

    // Uses the real Escape and pointer paths during a live action batch.
    private async Task ReviewPauseMenu()
    {
        try
        {
            double elapsed=_beatElapsed;int sequence=_timelineSequence,period=_period;
            GetViewport().PushInput(new InputEventKey { Keycode=Key.Escape,Pressed=true },true);
            await WaitFor(()=>_hostState=="playback-paused","Esc 未暂停当前批次");
            await ToSignal(GetTree().CreateTimer(.2),SceneTreeTimer.SignalName.Timeout);
            if(!_pauseMenu.Visible||!_playbackPaused||_beatElapsed!=elapsed||_timelineSequence!=sequence||_period!=period)
                throw new Exception("菜单打开后演出仍在前进");
            if(!new Rect2(Vector2.Zero,Size).Encloses(_pauseCard.GetGlobalRect()))throw new Exception("菜单超出视口");
            await PointerClick(_menuSave);
            await WaitFor(()=>_hostState=="save-queued","菜单保存请求没有排队");
            await Screenshot(System.IO.Path.ChangeExtension(_smoke!,null)+"-pause-menu.png");
            GetViewport().PushInput(new InputEventKey { Keycode=Key.Escape,Pressed=true },true);
            if(_pauseMenu.Visible||_playbackPaused)throw new Exception("Esc 没有恢复观看");
            await WaitFor(()=>_hostState is "presenting" or "advancing","菜单关闭后没有恢复宿主");
            await PointerClick(_save);
            await WaitFor(()=>_hostState=="playback-paused","顶部菜单按钮没有暂停");
            if(!_pauseMenu.Visible||!_playbackPaused)throw new Exception("顶部菜单按钮未打开菜单");
            await PointerClick(_menuContinue);
            if(_pauseMenu.Visible||_playbackPaused)throw new Exception("继续观看按钮未恢复演出");
            _pauseMenuChecked=true;
        }
        catch(Exception e){GD.PrintErr(e);QuitGame();GetTree().Quit(3);}
    }
}
