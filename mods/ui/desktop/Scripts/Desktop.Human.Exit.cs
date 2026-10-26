using Godot;
using System.Text.Json.Nodes;

public partial class Desktop
{
    private bool _humanCycleActive;
    private Button _humanDiscard=null!;
    private void BuildHumanExit()
    {
        _humanDiscard=Button(_pauseCard.GetChild<VBoxContainer>(0),"放弃未保存进度并退出",()=>
            ConfirmHuman("放弃本回合未保存进度",$"最近保存：第 {_savedHumanPeriod} 期完整边界。\n确认后保留这个存档，放弃之后的操作并退出。",()=>
            {
                SendHumanRow(new JsonObject { ["command"]="discard_progress",["confirm"]=true });
                _closing=true;_playbackPaused=true;
            }));
        HumanArt.Style(_humanDiscard,"close");_humanDiscard.Visible=false;
    }
    private string HumanExitInfo=>$"本回合尚未完成，保存以完整回合为边界。\n最近保存：第 {_savedHumanPeriod} 期。\n可继续操作完成本回合，或放弃未保存进度退出。";
    private void ShowHumanExitChoice()
    {
        if(!HumanMode)return;
        _playbackPaused=true;_pauseMenu.Visible=true;CloseDrawers();
        _resumeAfterMenu=true;_menuAutoRunning=_autoRunning;
        _pauseInfo.Text=HumanExitInfo;
        _menuContinue.Text="继续本回合";_menuQuit.Visible=false;_humanDiscard.Visible=true;
        _menuContinue.Disabled=_menuSave.Disabled=false;
        Send("pause");
    }
}
