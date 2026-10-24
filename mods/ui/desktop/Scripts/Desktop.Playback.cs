using Godot;
using System;
using System.Collections.Generic;
using System.Linq;
using System.IO;
using System.Text.Json;
using System.Text.Json.Nodes;
using static WorldMap;

public partial class Desktop
{
    private readonly Queue<JsonObject> _beats=new();
    private JsonObject? _playingBeat,_pendingFrame;
    private int _timelineSequence,_timelinePeriod,_beatNumber,_beatTotal;
    private bool _playbackPaused=true,_autoFollow=true,_awaitingFrame;
    private double _beatElapsed;
    private float _playbackSpeed=1;
    private PanelContainer _banner=null!;
    private Label _bannerTitle=null!,_bannerText=null!;
    private OptionButton _speedControl=null!;
    private readonly Dictionary<string,int> _playedPhases=new();
    private readonly List<string> _actorBanners=new();
    private readonly HashSet<string> _phaseShots=new();
    private bool _phaseCaptureBusy,_demoRecordingStarted;
    private int _recordedBattlePhases,_recordedMarchPhases;
    private bool HasTimeline=>_playingBeat!=null||_beats.Count>0||_awaitingFrame;

    private void BuildPlaybackControls(HBoxContainer top)
    {
        var follow=new CheckButton { Text="跟随",ButtonPressed=true,FocusMode=FocusModeEnum.None };
        follow.Toggled+=value=>_autoFollow=value;top.AddChild(follow);
        var speed=new OptionButton { FocusMode=FocusModeEnum.None };_speedControl=speed;
        string[] names={"细看 0.5×","慢速 1×","常速 2×","快速 4×","快进 16×"};
        foreach(var name in names)speed.AddItem(name);
        speed.Selected=1;speed.ItemSelected+=index=>_playbackSpeed=new[]{.5f,1,2,4,16}[(int)index];top.AddChild(speed);
        _banner=new PanelContainer { MouseFilter=MouseFilterEnum.Ignore,ZIndex=10 };
        var style=new StyleBoxFlat { BgColor=new Color(.055f,.10f,.11f,.94f),BorderColor=new Color("8d9471"),
            ContentMarginLeft=20,ContentMarginRight=20,ContentMarginTop=10,ContentMarginBottom=12 };
        style.SetBorderWidthAll(1);style.SetCornerRadiusAll(6);_banner.AddThemeStyleboxOverride("panel",style);
        _banner.SetAnchorsAndOffsetsPreset(LayoutPreset.CenterTop);_banner.OffsetLeft=-340;_banner.OffsetRight=340;
        _banner.OffsetTop=78;_banner.OffsetBottom=204;AddChild(_banner);
        var box=new VBoxContainer { MouseFilter=MouseFilterEnum.Ignore };_banner.AddChild(box);
        _bannerTitle=new Label { Text="观天下 · 等待开局",MouseFilter=MouseFilterEnum.Ignore,HorizontalAlignment=HorizontalAlignment.Center };
        _bannerTitle.AddThemeFontSizeOverride("font_size",23);_bannerTitle.AddThemeColorOverride("font_color",new Color("ead9a7"));box.AddChild(_bannerTitle);
        _bannerText=new Label { Text="点击继续，按国家和动作慢速观看",AutowrapMode=TextServer.AutowrapMode.WordSmart,
            HorizontalAlignment=HorizontalAlignment.Center,MouseFilter=MouseFilterEnum.Ignore,CustomMinimumSize=new Vector2(0,64),MaxLinesVisible=3,TextOverrunBehavior=TextServer.OverrunBehavior.TrimEllipsis };box.AddChild(_bannerText);
    }
    private void TogglePlayback()
    {
        if(HasTimeline)
        {
            _playbackPaused=!_playbackPaused;Send(_playbackPaused?"pause":"resume");
        }
        else {_playbackPaused=_running;Send(_running?"pause":"resume");}
        RefreshPlaybackControls();
    }
    private void StepPlayback() { _playbackPaused=false;Send("step"); }
    private void ReceiveTimeline(JsonObject row)
    {
        if(HasTimeline)throw new Exception("上一期演出尚未确认，拒绝覆盖播放状态");
        _timelineSequence=I(row["sequence"]);_timelinePeriod=I(row["period"]);_beatNumber=0;
        _map.ClearActivity();
        foreach(var beat in row["beats"]!.AsArray())_beats.Enqueue(beat!.AsObject());
        _beatTotal=_beats.Count;StartNextBeat();
    }
    private void StartNextBeat()
    {
        if (_beats.Count == 0){FinishPlayback();return;}
        _playingBeat=_beats.Dequeue();_beatElapsed=0;_beatNumber++;
        _map.BeginBeat(_playingBeat,_autoFollow);
        string phase=S(_playingBeat["phase"]);
        DriveBubbles(_playingBeat,phase);
        _playedPhases[phase]=_playedPhases.GetValueOrDefault(phase)+1;
        _bannerTitle.Text=S(_playingBeat["turn_title"]);
        _bannerText.Text=$"{_playingBeat["title"]} · {_beatNumber}/{_beatTotal}\n{_playingBeat["text"]}";
        _banner.TooltipText=S(_playingBeat["text"])+"\n"+S(_playingBeat["source"]);
        if(phase=="actor"&&_smoke!=null)_actorBanners.Add(_bannerTitle.Text);
        if(_smoke!=null&&OS.GetEnvironment("ZHANGUO_DESKTOP_SMOKE_TOUR")=="1")
        {
            bool battlePhase=new[]{"battle","casualties","occupation"}.Contains(phase);
            bool marchPhase=new[]{"march","engage","arrival"}.Contains(phase);
            bool slow=(battlePhase&&_recordedBattlePhases<10)||(marchPhase&&_recordedMarchPhases<4);
            if(phase is "production" or "maintenance")slow=!_phaseShots.Contains(phase);
            _playbackSpeed=slow?1:96;
            if(slow&&battlePhase)_recordedBattlePhases++;
            if(slow&&marchPhase)_recordedMarchPhases++;
            if(slow&&!_demoRecordingStarted){_demoRecordingStarted=true;GD.Print("DESKTOP_PHASE_DEMO_START");}
        }
        if(_smoke!=null)_speedControl.Text=_playbackSpeed==1?"慢速 1×":"快进 96×";
        RefreshPlaybackControls();
    }
    private void DriveBubbles(JsonObject beat,string phase)
    {
        if(phase!="actor")return;
        string actor=S(beat["actor"]),version=_models.GetValueOrDefault(actor,"");
        // LLM dialogue arrived live; replaying action receipts here would
        // overwrite it and assign the final speaker's text to another nation.
        if(!SpeechBubbles.IsRuleVersion(version))return;
        _bubbles.SetSpeaker(actor,_map.NationName(actor),version,_map.NationColor(actor));
        _bubbles.ShowRuleTurn();
    }

    private void ProcessPlayback(double delta)
    {
        if(_closing||_playingBeat==null||_playbackPaused||_phaseCaptureBusy)return;
        _beatElapsed+=delta*_playbackSpeed;
        _bubbles.SetSpeed(_playbackSpeed);
        double duration=F(_playingBeat["duration"]);
        string phase=S(_playingBeat["phase"]);
        string shot=phase=="maintenance"&&_playingBeat["pops"]?.AsArray().Any(p=>S(p!["resource"])=="hp")==true?"healing":phase;
        if(_smoke!=null&&_beatElapsed>=duration*.3&&new[]{"actor","build","recruit","march","engage","production","maintenance","healing","battle","casualties","occupation","complete"}.Contains(shot)&&_phaseShots.Add(shot))
        {
            _beatElapsed=duration*.45;_map.BeatProgress(.45f);_phaseCaptureBusy=true;_=CapturePhase(shot);return;
        }
        _map.BeatProgress((float)Math.Clamp(_beatElapsed/Math.Max(.05,duration),0,1));
        if(_beatElapsed>=duration)StartNextBeat();
    }
    private async System.Threading.Tasks.Task CapturePhase(string phase)
    {
        try
        {
            await ToSignal(GetTree().CreateTimer(.7),SceneTreeTimer.SignalName.Timeout);
            if(_closing)return;
            await Screenshot(Path.ChangeExtension(_smoke!,null)+"-"+phase+".png");
        }
        catch(Exception e){if(!_closing){GD.PrintErr(e);GetTree().Quit(3);}}
        finally {_phaseCaptureBusy=false;}
    }
    private void ApplyFrame(JsonObject frame)
    {
        if(_initialArmies<0)
        {
            _initialArmies=frame["armies"]!.AsArray().Count;
            _captureAfterPeriod=I(frame["period"])+3;_normalOpening=I(frame["period"])==0;
        }
        _period=I(frame["period"]);_map.SetFrame(frame);UpdatePanels(frame);
        if(_smoke!=null&&_period>=_captureAfterPeriod&&!_capturing){_capturing=true;_=Capture();}
    }
    private void FinishPlayback()
    {
        if(_closing)return;
        _playingBeat=null;_map.EndBeat();_bubbles.HideSoon();
        if(_pendingFrame==null){_awaitingFrame=true;return;}
        ApplyFrame(_pendingFrame);_pendingFrame=null;_awaitingFrame=false;
        _bannerTitle.Text=$"第 {_timelinePeriod+1} 回合 · 全部结算完成";
        _bannerText.Text="所有战斗、生产、补给与落成已展示";
        try {_host!.StandardInput.WriteLine(JsonSerializer.Serialize(new {command="playback_ack",generation=_generation,sequence=_timelineSequence}));}
        catch(Exception e){Error(e.Message);}
        if(!_running)_playbackPaused=true;
        RefreshPlaybackControls();
    }
    private void RefreshPlaybackControls()
    {
        if(_play==null)return;
        _play.Text=HasTimeline?(_playbackPaused?"继续演出":"暂停演出"):_running?"暂停":"继续";
        _step.Disabled=_closing||_running||HasTimeline;_play.Disabled=_closing;
        if(HasTimeline)_status.Text=$"第 {_timelinePeriod+1} 回合 · {(_playbackPaused?"演出暂停":"演出中")} · {_beatNumber}/{_beatTotal}";
    }
}
