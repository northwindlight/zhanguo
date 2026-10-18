"""Subject relations; the scenario may appoint a privileged tributary league."""
from dataclasses import dataclass, replace

from engine.identifiers import content_id


@dataclass(frozen=True)
class Vassal:
    subject: str
    overlord: str
    kind: str = 'ordinary'
    tribute_percent: int = 15


class Vassalage:
    def configure_subject_alliance(self, overlord, bloc, name, tribute_percent, found_fee=0, auto_accept=False,
                                   forbid_subjection=False, lock_chief=False):
        self._write();content_id(overlord);content_id(bloc)
        if type(tribute_percent) is not int or not 0<=tribute_percent<=100:raise ValueError('Invalid tribute percent')
        if (type(found_fee) is not int or found_fee<0
            or any(type(v) is not bool for v in (auto_accept,forbid_subjection,lock_chief))):raise ValueError('Invalid subject alliance policy')
        self._subject_alliance={'overlord':overlord,'bloc':bloc,'name':name,'tribute_percent':tribute_percent,
                                'found_fee':found_fee,'auto_accept':auto_accept,
                                'forbid_subjection':forbid_subjection,'lock_chief':lock_chief}

    def vassal(self, actor):return self._vassals.get(actor)

    def diplomatic_authority(self, actor):
        subject=self.vassal(actor)
        return not subject or subject.kind!='ordinary'

    def _sovereign(self, actor):
        seen=set()
        while (subject:=self.vassal(actor)) and subject.kind=='ordinary':
            if actor in seen:raise ValueError('附庸关系不能成环')
            seen.add(actor);actor=subject.overlord
        return actor

    def _dependent_members(self, members):
        result=list(dict.fromkeys(members))
        while True:
            added=[s.subject for s in self._vassals.values() if s.kind=='ordinary' and s.overlord in result and s.subject not in result]
            if not added:return tuple(result)
            result.extend(added)

    def _sync_subject_alliance(self):
        # Special subjects are exactly the peers of the scenario's appointed
        # chief. Leaving/transfer/dissolution ends that special relationship.
        rule=self._subject_alliance
        bloc=self.alliance(rule['overlord']) if rule else None
        privileged=bloc if bloc and bloc.chief==rule['overlord'] else None
        transferred=[]
        if privileged:
            expanded=self._dependent_members(privileged.members)
            if expanded!=privileged.members:
                transferred=[s for s in self._vassals.values() if s.kind=='ordinary' and s.subject in expanded]
                privileged=replace(privileged,members=expanded)
                self._alliances[privileged.id]=privileged
        expected=set(privileged.members)-{privileged.chief} if privileged else set()
        self._vassals={n:s for n,s in self._vassals.items()
            if s.kind=='ordinary' or n in expected}
        if privileged:
            if privileged.name!=rule['name']:
                if any(b.id!=privileged.id and b.name==rule['name'] for b in self._alliances.values()):
                    raise ValueError('剧本宗主联盟名称已被其他联盟占用，须先解决重名')
                self._alliances[privileged.id]=replace(privileged,name=rule['name'])
            for actor in sorted(expected):
                self._vassals[actor]=Vassal(actor,privileged.chief,'tributary',rule['tribute_percent'])
            if transferred:
                names='、'.join(self._label('nation/'+s.subject) for s in transferred)
                self._effects.append(('notice',f'剧本宗主联盟「{rule["name"]}」接收成员原附庸：{names}，'
                    f'全部移交 {self._label("nation/"+privileged.chief)}，转为直接上贡{rule["tribute_percent"]}%、保留正常盟友权力的特殊附庸；原宗主不再保留附庸关系。',privileged.members,True))

    def production_shares(self):
        return {s.subject:{'recipient':s.overlord,'percent':s.tribute_percent}
                for s in self._vassals.values()}

    def _validate_submission(self, overlord, subject, *, peace=False):
        self._living(overlord);self._living(subject)
        rule=self._subject_alliance
        if rule and rule['forbid_subjection'] and subject==rule['overlord']:
            raise ValueError('剧本指定宗主不能成为附庸，只能保持独立地位或灭国')
        if overlord==subject or not self.diplomatic_authority(overlord) or self.vassal(subject):
            raise ValueError('宗主须有外交权，双方须为不同国家，附庸不能重复称臣')
        cursor=overlord
        while relation:=self.vassal(cursor):
            if relation.overlord==subject:raise ValueError('附庸关系不能成环')
            cursor=relation.overlord
        if self.alliance(subject) and not peace:raise ValueError('须先退出原联盟，才能接受附庸关系')
        bloc=self.alliance(overlord)
        rule=self._subject_alliance
        if rule and overlord==rule['overlord'] and bloc and bloc.chief!=overlord:
            raise ValueError('剧本指定宗主须先独立或取得盟主身份，不能自动夺取别国盟主权')
        if not self._permits(subject,'accept.bloc') or not self._permits(overlord,'accept.bloc'):
            raise ValueError('双方政体须允许建立附属关系')

    def _secede_for_submission(self, subject):
        """An explicitly approved peace binds its representative, not all peers."""
        bloc=self.alliance(subject)
        if not bloc:return
        self._peaceful('bloc/'+bloc.id)
        members=tuple(n for n in bloc.members if n!=subject)
        if len(members)<2:
            self._drop_entity_pacts('bloc/'+bloc.id);del self._alliances[bloc.id]
        else:self._alliances[bloc.id]=replace(bloc,members=members,chief=members[0] if bloc.chief==subject else bloc.chief)
        self._sync_subject_alliance()
        self._effects.append(('notice',f'{self._label("nation/"+subject)} 按已接受的称臣和约退出原联盟；原盟友不随其成为附庸。',bloc.members,True))

    def _establish_vassal(self, overlord, subject):
        from .objects import Alliance
        # The scenario's tributary league centralizes all subordinate links;
        # even a member's newly acquired subject goes directly to its chief.
        rule=self._subject_alliance;bloc=self.alliance(overlord)
        if rule and bloc and bloc.chief==rule['overlord']:overlord=rule['overlord']
        self._validate_submission(overlord,subject)
        self._drop_entity_pacts(self.entity(subject))
        rule=self._subject_alliance
        if rule and overlord==rule['overlord']:
            bloc=self.alliance(overlord)
            if bloc:
                self._alliances[bloc.id]=replace(bloc,members=(*bloc.members,subject))
            else:
                self._name(rule['name'])
                if rule['bloc'] in self._alliances:raise ValueError('剧本宗主联盟编号已被占用')
                if rule['found_fee']:
                    cash={s.id:s.amount for s in self._call('native:resource-inspection','snapshot',overlord)}.get(self._currency(),0)
                    if cash<rule['found_fee']:raise ValueError('宗主资金不足，无法支付剧本要求的首次建盟费')
                    self._effects.append(('charge',overlord,rule['found_fee']))
                self._drop_entity_pacts(self.entity(overlord))
                self._alliances[rule['bloc']]=Alliance(rule['bloc'],rule['name'],(overlord,subject),overlord)
            self._sync_subject_alliance()
            self._membership_notice(self.alliance(overlord),formed=not bloc)
        else:
            released=[s.subject for s in self._vassals.values() if s.overlord==subject]
            for actor in released:del self._vassals[actor]
            if released:
                names='、'.join(self._label('nation/'+n) for n in released)
                self._effects.append(('notice',f'{self._label("nation/"+subject)} 成为别国附庸，原附庸 {names} 在本期结算释放，恢复独立外交并停止上贡。',
                    (subject,*released),True))
            self._vassals[subject]=Vassal(subject,overlord,'ordinary',self._tribute_rates['ordinary'])
        relation=self.vassal(subject)
        rate=relation.tribute_percent
        self._effects.append(('notice',f'{self._label("nation/"+subject)} 成为 {self._label("nation/"+overlord)} 的'
            f'{"特殊附庸" if relation.kind=="tributary" else "普通附庸"}，每期强制上交实际生产的{rate}%。',
            (overlord,subject),True))
        return relation

    def _apply_propose_vassalage(self, actor, target, *, approved=False):
        self._validate_submission(actor,target)
        self._peaceful(self.entity(actor),self.entity(target))
        routed=self._route(actor,'propose_vassalage',{'target':target},approved,(target,))
        if routed:return routed
        return self._offer('vassal',actor,self.entity(target),{},self.members(self.entity(actor))+(target,))

    def _apply_submit_vassalage(self, actor, target, *, approved=False):
        self._validate_submission(target,actor)
        self._peaceful(self.entity(actor),self.entity(target))
        rule=self._subject_alliance
        if rule and target==rule['overlord'] and rule['auto_accept']:
            bloc=self.alliance(target)
            if bloc and not approved:
                return self._vote(actor,'submit_vassalage',{'target':target},bloc,(actor,))
            return self._establish_vassal(target,actor)
        return self._offer('submission',actor,self.entity(target),{'overlord':target},(actor,*self.members(self.entity(target))))

    def _apply_release_vassal(self, actor, target, *, approved=False):
        relation=self.vassal(target)
        if not relation or actor not in (relation.overlord,self._sovereign(relation.overlord)):raise ValueError('只能释放本国普通附庸')
        if relation.kind!='ordinary':raise ValueError('特殊附庸按联盟规则和平退盟，不使用释放普通附庸')
        self._peaceful(self.entity(actor))
        routed=self._route(actor,'release_vassal',{'target':target},approved,(target,))
        if routed:return routed
        del self._vassals[target]
        self._effects.append(('notice',f'{self._label("nation/"+actor)} 释放附庸 {self._label("nation/"+target)}，其恢复外交权并停止上贡。',(actor,target),True))
        return relation

    def _apply_independence_war(self, actor, *, approved=False):
        relation=self.vassal(actor)
        if not relation or relation.kind!='ordinary':raise ValueError('只有普通附庸可以发动独立战争')
        overlord=relation.overlord;old_entity=self.entity(overlord)
        # Rebellion leaves the overlord's existing fronts, without ending the
        # wars of other countries. The rebel starts a real, separate front.
        del self._vassals[actor]
        rebels=set(self._dependent_members((actor,)))
        self._wars=[replace(w,attackers=tuple(n for n in w.attackers if n not in rebels),
                              defenders=tuple(n for n in w.defenders if n not in rebels)) for w in self._wars]
        self._wars=[w for w in self._wars if w.attackers and w.defenders]
        # An old personal truce cannot erase the explicit right to rebel.
        self._truces.pop(tuple(sorted((self.entity(actor),old_entity))),None)
        war=self._apply_declare_war(actor,overlord,approved=True)
        self._effects.append(('notice',f'{self._label("nation/"+actor)} 向宗主 {self._label("nation/"+overlord)} 发动独立战争，停止上贡并脱离宗主外交实体；须承担战争后果。',war.attackers+war.defenders,True))
        return war
