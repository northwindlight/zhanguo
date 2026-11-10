"""Create standalone figures from the frozen economic training report CSVs."""
import argparse
import csv
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm
import numpy as np


def read(root,name):return list(csv.DictReader((root/'tables'/name).open()))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();root=args.out;figures=root/'figures';figures.mkdir(exist_ok=True)
    rows=read(root,'rounds.csv');x=[int(r['round'])for r in rows]
    values=lambda key:np.array([float(r[key])for r in rows])
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    def save(fig,name):
        fig.savefig(figures/(name+'.png'),dpi=160,bbox_inches='tight')
        svg=figures/(name+'.svg');fig.savefig(svg,bbox_inches='tight')
        svg.write_text('\n'.join(line.rstrip()for line in svg.read_text().splitlines())+'\n')
        plt.close(fig)
    fig,axes=plt.subplots(2,1,figsize=(11,8),sharex=True)
    for seed,color in [(8101,'#2878b5'),(8102,'#df7651')]:
        axes[0].plot(x,values(f'S_ratio_{seed}')*100,'o-',label=f'{seed}, greedy, 150 turns',color=color)
    for y,label in [(100,'Teacher = 100%'),(150,'Acceptance total = 150%')]:
        axes[0].axhline(y,ls='--',lw=1,color='grey');axes[0].text(.7,y+2,label,color='grey')
    axes[0].set_ylim(25,160);axes[0].set_ylabel('Total S / teacher (%)');axes[0].legend(loc='lower right')
    axes[0].set_title('20 completed PPO rounds: selection environments, not unseen test worlds')
    for key,label,color in [('worst_environment_total','Worst environment total','#756bb1'),
                            ('worst_seat','Worst seat cumulative S','#31a354'),('worst_tail','Worst seat final-50-turn S','#e6550d')]:
        axes[1].plot(x,values(key)*100,'o-',label=label,color=color)
    axes[1].axhline(90,color='grey',ls=':',label='Current code near-gate (seat/tail): 90%')
    axes[1].set_ylabel('Student / teacher (%)');axes[1].set_xlabel('PPO round');axes[1].legend(fontsize=8,ncol=2)
    axes[1].set_ylim(-3,125);axes[1].set_xticks(x)
    for ax in axes:ax.grid(alpha=.18)
    fig.tight_layout();save(fig,'performance')

    seats=read(root,'evaluation_seats.csv');labels=[(8101,f'scenario:n{i}')for i in [1,3,5,7]]+[(8102,f'scenario:n{i}')for i in [0,2,4,6]]
    fig,axes=plt.subplots(1,2,figsize=(14,11),sharey=True)
    for ax,key,title in zip(axes,['S_ratio','tail_ratio'],['Cumulative S / teacher','Final 50-turn S / teacher']):
        data=np.array([[float(next(s[key]for s in seats if int(s['round'])==n and (int(s['seed']),s['actor'])==label))for label in labels]for n in x])
        m=ax.imshow(data,aspect='auto',cmap='RdYlGn',norm=TwoSlopeNorm(vmin=0,vcenter=1,vmax=2))
        ax.set_xticks(range(8),[f'{seed}\nn{actor[-1]}'for seed,actor in labels]);ax.set_yticks(range(len(x)),x)
        ax.set_title(title+' (%)');ax.axvline(3.5,color='white',lw=2)
        for i in range(data.shape[0]):
            for j in range(data.shape[1]):
                ax.text(j,i,f'{data[i,j]*100:.0f}',ha='center',va='center',fontsize=7,color='black')
    axes[0].set_ylabel('PPO round');fig.colorbar(m,ax=axes.ravel().tolist(),shrink=.6,label='Ratio; color capped at 2.0, labels are uncapped')
    fig.suptitle('Every focal seat: gains in strong seats do not protect weak seats',y=.94)
    save(fig,'seats')

    fig,axes=plt.subplots(2,2,figsize=(13,8))
    ax=axes[0,0]
    for key,label in [('loss','Total loss'),('weighted_value_loss','0.5 x value loss'),('weighted_execution_loss','0.1 x execution loss')]:
        ax.plot(x,values(key),'o-',label=label)
    ax.set_yscale('log');ax.set_title('Different on-policy data each round');ax.legend(fontsize=8);ax.set_ylabel('Loss (log scale)')
    ax=axes[0,1]
    failed=values('failed_fraction')*100;success=values('successful_project_fraction')*100;finish=100-failed-success
    ax.stackplot(x,failed,success,finish,labels=['Failed project','Successful build/recruit','Finish'],colors=['#df7651','#66b99f','#b3cde3'])
    ax.set_ylim(0,100);ax.set_ylabel('Decisions (%)');ax.set_title('Actual sampled action outcomes');ax.legend(loc='lower left',fontsize=8)
    ax=axes[1,0]
    ax.plot(x,values('kl')*100,'o-',label='Mean KL x100');ax.plot(x,values('clip_fraction')*100,'o-',label='Clipped examples (%)')
    ax.axhline(3,color='grey',ls='--',label='KL target x100');ax.legend(fontsize=8);ax.set_title('Adaptive KL; all 20 rounds have 100% coverage')
    ax=axes[1,1]
    points=ax.scatter(values('loss'),values('worst_environment_total')*100,c=x,cmap='viridis',s=55)
    for n in [1,10,13,18,20]:
        i=n-1;ax.annotate(f'R{n}',(float(rows[i]['loss']),float(rows[i]['worst_environment_total'])*100),xytext=(5,4),textcoords='offset points')
    ax.set_xlabel('Training loss');ax.set_ylabel('Worst environment total / teacher (%)');ax.set_title('Loss is not fixed-world economic performance')
    fig.colorbar(points,ax=ax,label='Round')
    for ax in axes.ravel():ax.grid(alpha=.15)
    for ax in [axes[0,0],axes[0,1],axes[1,0]]:ax.set_xlabel('PPO round')
    fig.tight_layout();save(fig,'optimization')

    history=read(root,'evaluation_history.csv');fig,axes=plt.subplots(2,2,figsize=(12,8))
    for col,seed in enumerate([8101,8102]):
        for n,color in [(10,'#2878b5'),(20,'#df7651')]:
            subset=[r for r in history if int(r['round'])==n and int(r['seed'])==seed];turn=[int(r['turn'])for r in subset]
            axes[0,col].plot(turn,[float(r['student_S'])/float(r['teacher_S'])*100 for r in subset],label=f'R{n}',color=color)
            axes[1,col].plot(turn,[float(r['student_land'])for r in subset],label=f'R{n} focal seats',color=color)
            if n==10:axes[1,col].plot(turn,[float(r['teacher_land'])for r in subset],label='Teacher focal seats',ls='--',color='grey')
        axes[0,col].axhline(100,ls='--',color='grey');axes[0,col].set_title(f'{seed}: cumulative S ratio (%)')
        axes[1,col].set_title(f'{seed}: total owned land of 4 focal seats')
        for ax in axes[:,col]:ax.axvline(100,ls=':',color='grey');ax.grid(alpha=.2);ax.legend(fontsize=8);ax.set_xlabel('Game turn')
    fig.suptitle('Long-game structure: owned land is a diagnostic, not the reward');fig.tight_layout();save(fig,'long_game')

    bc=read(root,'bc_epochs.csv');fig,axes=plt.subplots(1,2,figsize=(12,4))
    xx=[int(r['epoch'])for r in bc]
    axes[0].plot(xx,[float(r['train_loss'])for r in bc],label='Train');axes[0].plot(xx,[float(r['valid_loss'])for r in bc],label='Validation')
    axes[0].set_ylabel('BC loss');axes[0].legend()
    for key,label in [('valid_accuracy','Overall'),('valid_build_accuracy','Build'),('valid_recruit_accuracy','Recruit'),('valid_finish_accuracy','Finish')]:
        axes[1].plot(xx,[float(r[key])*100 for r in bc],label=label)
    axes[1].set_ylabel('Validation action accuracy (%)');axes[1].legend(fontsize=8)
    for ax in axes:ax.set_xlabel('BC epoch');ax.grid(alpha=.2)
    fig.suptitle('14-world BC: build classification remains substantially harder');fig.tight_layout();save(fig,'bc')
    print(figures)


if __name__=='__main__':main()
