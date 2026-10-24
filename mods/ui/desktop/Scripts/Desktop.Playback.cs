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
        _banner.OffsetTop=72;_banner.OffsetBottom=169;AddChild(_banner);
        var box=new VBoxContainer { MouseFilter=MouseFilterEnum.Ignore };_banner.AddChild(box);
        _bannerTitle=new Label { Text="观天下 · 等待开局",MouseFilter=MouseFilterEnum.Ignore,HorizontalAlignment=HorizontalAlignment.Center };
        _bannerTitle.AddThemeFontSizeOverride("font_size",23);_bannerTitle.AddThemeColorOverride("font_color",new Color("ead9a7"));box.AddChild(_bannerTitle);
        _bannerText=new Label { Text="点击继续，按国家和动作慢速观看",AutowrapMode=TextServer.AutowrapMode.WordSmart,
            HorizontalAlignment=HorizontalAlignment.Center,MouseFilter=MouseFilterEnum.Ignore,CustomMinimumSize=new Vector2(640,44) };box.AddChild(_bannerText);
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
        _bannerText.Text=$"{_playingBeat["title"]} · 动作 {_beatNumber}/{_beatTotal}\n{_playingBeat["text"]}";
        _banner.TooltipText=S(_playingBeat["source"]);
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
    // Nation turns speak through the bubble layer: each enacted action is one
    // streamed body bubble, the turn close is a distinct summary bubble, and
    // settlement narration dims the layer instead of replacing the speaker.
    // Rule-AI demo nations get preset streamed "thinking" and a frame-derived
    // context line; LLM nations keep their real streamed thinking and context.
    private void DriveBubbles(JsonObject beat,string phase)
    {
        string actor=S(beat["actor"]),text=S(beat["text"]);
        string Context()=>beat["context"]?.ToString() is {Length:>0} found?found:null;
        switch(phase)
        {
            case "actor":
            {
                string nation=_map.Frame?["names"]?[actor]?.ToString()??actor;
                string version=_models.GetValueOrDefault(actor,"");
                _bubbles.SetSpeaker(actor,nation,version,_map.NationColor(actor));
                bool preset=SpeechBubbles.IsRuleVersion(version);
                _bubbles.SetStats(preset?FrameContext():Context(),null,null);
                if(preset)
                {
                    _bubbles.Begin("thinking");
                    _bubbles.Append("thinking",PresetThinking(actor,nation));
                }
                _bubbles.Begin("body");
                _bubbles.Append("body",text);
                break;
            }
            case "plan" or "trade" or "build" or "recruit" or "order" or "rejected" or "action" or "arrival" or "retreat":
                _bubbles.Begin("body");
                _bubbles.Append("body",text);
                break;
            case "turn-end":
                _bubbles.SetStats(Context(),
                    beat["tokens"] is JsonNode tokens?(int?)I(tokens):null,
                    beat["turn_seconds"] is JsonNode seconds?(double?)F(seconds):null);
                _bubbles.Begin("turn_end");
                _bubbles.Append("turn_end",text);
                break;
            default:
                _bubbles.Park();
                break;
        }
    }

    // Preset rule-AI "thinking" for the streaming demo; numbers are real frame
    // facts, phrasing is canned flavor so the offline demo plays like an LLM.
    private string PresetThinking(string actor,string nation)
    {
        var frame=_map.Frame;
        var row=frame?["nations"]?.AsArray().FirstOrDefault(n=>S(n!["id"])==actor);
        int land=I(row?["land"]);
        double food=0,wood=0,gold=0;
        foreach(var s in row?["stocks"]?.AsArray()??new JsonArray())
        {
            string name=S(s!["name"]);
            if(name.Contains('粮'))food=F(s["amount"]);
            else if(name.Contains('木'))wood=F(s["amount"]);
            else if(name.Contains('金')&&!name.Contains("金矿"))gold=F(s["amount"]);
        }
        int seed=actor.Aggregate(0,(hash,c)=>hash*31+c)^(_period*7919);
        seed&=int.MaxValue;
        string[] openers=
        {
            $"{nation}第{_period+1}期盘点：国土{land}格，粮{food:0}、木{wood:0}、金{gold:0}。先把粮仓补满，再看邻国动向。",
            $"开局推演：{land}格国土，存粮{food:0}。粮道是根本，扩产要趁早；兵力可缓，民兵足以守土。",
            $"环视列国，{nation}以{land}格立身。金{gold:0}尚可支撑数期，宜先固根本、再图进取。",
        };
        string[] closers=
        {
            "综合判断：稳步扩产，保持边境警戒，不轻启战端。",
            "先看市集行情，再定采购与施工的先后次序。",
            "若邻国有异动，立即转入防御并征召民兵。",
        };
        return openers[seed%openers.Length]+"\n"+closers[(seed/3)%closers.Length];
    }

    private string FrameContext()
    {
        var frame=_map.Frame;
        int events=frame?["events"]?.AsArray().Count??0;
        int market=frame?["market"]?.AsArray().Count??0;
        int nations=frame?["nations"]?.AsArray().Count??0;
        return $"八国{nations}家 · 本期事件{events}条 · 市集{market}种（规则AI演示）";
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
