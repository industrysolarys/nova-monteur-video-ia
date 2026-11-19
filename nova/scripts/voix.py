"""Voix Fish Audio : clonage (facultatif), synthèse phrase par phrase, alignement par la réécoute Whisper, tableau des durées.
Usage :
  python3 voix.py <dossier> --cloner [--secondaire]   crée un clone depuis la voix isolée, teste une synthèse, enregistre l'id dans la fiche
  python3 voix.py <dossier> [--force] [--reprendre] [S1 S2 ...]   génère les lignes de la fiche (celles déjà générées avec le même texte sont gardées)
  python3 voix.py <dossier> --supprimer    supprime le clone de la fiche (clones jetables, voir voix.md)
Chaque phrase est réécoutée par Whisper (langue entendue + mots, sans tenir compte des homophones) : une phrase avec un accent
étranger ou des mots sautés/ajoutés est régénérée automatiquement (voix.reprises, 2 par défaut) et la meilleure prise est gardée.
--reprendre applique aussi la reprise aux phrases déjà générées.
Fish Audio ne renvoie pas d'horodatage : l'alignement caractère par caractère de tts/<id>.json (même format qu'avant :
characters, character_start_times_seconds, character_end_times_seconds) est reconstruit à partir des mots horodatés de la réécoute.
Lit fiche.json : voix{id,repli_id,vitesse_max,modele?}, lignes[{id,texte,debut,fin_fenetre,vitesse?}]. Écrit tts/<id>.mp3 et tts/<id>.json."""
import difflib,hashlib,json,os,re,subprocess,sys,unicodedata,urllib.error,urllib.parse,urllib.request
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from common import *
API='https://api.fish.audio'
MODELE='s2.1-pro'  # en-tête « model » de Fish Audio ; voix.modele dans la fiche pour en changer
FP=FF[:-6]+'ffprobe' if FF.endswith('ffmpeg') else 'ffprobe'

def appel(url,corps=None,methode='POST',entetes=None):
    k=cle_fish();h={} if k=='cloud' else {'Authorization':'Bearer '+k};h.update(entetes or {})  # 'cloud' = secret réseau : le proxy ajoute l'en-tête
    h.setdefault('User-Agent','nova/1.0')
    req=urllib.request.Request(url,corps,h,method=methode)
    try:return urllib.request.urlopen(req,timeout=180)
    except urllib.error.HTTPError as e:
        return e
def _code(r):return getattr(r,'status',None) or getattr(r,'code',None)

LANGUE='fr'  # langue cible : lue dans la fiche (langue_cible) par main()

def synthese(voix,texte,vitesse=1.0,avant=None,apres=None,stabilite=0.5):
    """-> (200, octets mp3) ou (code, message). avant/apres ignorés (Fish Audio n'a pas de phrases voisines).
    stabilite 0,5 = température 0,7 (réglage par défaut de Fish) ; plus stable = température plus basse."""
    c={"text":texte,"reference_id":voix,"format":"mp3","mp3_bitrate":128,"normalize":True,"latency":"normal",
       "language":LANGUE,"temperature":round(max(0.3,min(0.9,1.0-0.6*stabilite)),2),"top_p":0.7,"prosody":{"speed":vitesse,"volume":0}}
    r=appel(f"{API}/v1/tts",json.dumps(c).encode(),entetes={'Content-Type':'application/json','model':MODELE})
    if _code(r)!=200:return _code(r),r.read().decode('utf-8','ignore')[:300]
    return 200,r.read()

def duree_mp3(p):
    try:return float(subprocess.run([FP,'-v','error','-show_entries','format=duration','-of','csv=p=0',p],capture_output=True,text=True).stdout.strip() or 0)
    except Exception:return 0.0

def alignement(texte,mots,duree):
    """Alignement caractère par caractère (format de l'ancien tts/<id>.json) à partir des mots horodatés de la réécoute.
    mots=[{word,start,end}] relatifs au début de la phrase. Mots non reconnus : interpolés entre leurs voisins reconnus.
    Sans aucun mot entendu : répartition au prorata des caractères sur la durée du mp3."""
    spans=[(m.start(),m.end()) for m in re.finditer(r'\S+',texte)]
    n=len(texte);st=[0.0]*n;en=[0.0]*n
    if not spans:return {'characters':list(texte),'character_start_times_seconds':st,'character_end_times_seconds':en}
    att=[(p,k) for k,(a,b) in enumerate(spans) for p in _mots(texte[a:b])]  # « aujourd'hui » donne deux morceaux
    ent=[]
    for w in mots or []:
        ps=_mots(w['word']);d=(w['end']-w['start'])/max(1,len(ps))
        ent+=[(p,w['start']+i*d,w['start']+(i+1)*d) for i,p in enumerate(ps)]
    t=[None]*len(spans)
    for op,i1,i2,j1,j2 in difflib.SequenceMatcher(None,[_son(p) for p,_ in att],[_son(p) for p,_,_ in ent],autojunk=False).get_opcodes():
        if op=='equal' or (op=='replace' and i2-i1==j2-j1):
            for x,y in zip(range(i1,i2),range(j1,j2)):
                k=att[x][1];a0,b0=ent[y][1],ent[y][2]
                t[k]=(a0,b0) if t[k] is None else (min(t[k][0],a0),max(t[k][1],b0))
    fin_parole=max([b for _,_,b in ent],default=0) or duree
    if all(x is None for x in t):
        tot=sum(b-a for a,b in spans);pos=0.0
        for k,(a,b) in enumerate(spans):d=duree*(b-a)/tot;t[k]=(pos,pos+d);pos+=d
    else:
        for k in range(len(spans)):
            if t[k] is not None:continue
            g=max([j for j in range(k) if t[j] is not None],default=None);h=min([j for j in range(k+1,len(spans)) if t[j] is not None],default=None)
            a0=t[g][1] if g is not None else 0.0;b0=t[h][0] if h is not None else max(fin_parole,a0+0.2)
            libres=list(range((g if g is not None else -1)+1,h if h is not None else len(spans)))
            pas=(b0-a0)/max(1,len(libres));r=libres.index(k);t[k]=(a0+r*pas,a0+(r+1)*pas)
    prec=0.0
    for k,(a,b) in enumerate(spans):
        a0,b0=t[k];a0=max(a0,prec);b0=max(b0,a0+0.01)
        for c in range(a,b):st[c]=a0+(b0-a0)*(c-a)/(b-a);en[c]=a0+(b0-a0)*(c-a+1)/(b-a)
        prec=en[b-1];suivant=spans[k+1][0] if k+1<len(spans) else n
        for c in range(b,suivant):st[c]=en[c]=prec  # espaces et ponctuation détachée
    for c in range(0,spans[0][0]):st[c]=en[c]=t[0][0]
    return {'characters':list(texte),'character_start_times_seconds':st,'character_end_times_seconds':en}

# ---------------------------------------------------------------- réécoute d'une phrase générée
NOMBRES=set("zéro un une deux trois quatre cinq six sept huit neuf dix onze douze treize quatorze quinze seize vingt trente quarante cinquante soixante cent cents mille et pour "
  "null eins eine einer einem einen zwei drei vier fünf sechs sieben acht neun zehn elf zwölf zwanzig dreißig vierzig fünfzig hundert tausend und prozent "  # allemand (13/10/2026 : « drei » lu « 3 » comptait comme un écart)
  "zero uno una due tre quattro cinque sei sette otto nove dieci undici dodici venti trenta quaranta cinquanta cento mila e per cento".split())  # italien
def _mots(t):return re.sub(r"[^a-zà-ÿß0-9%]+"," ",t.lower().replace("'"," ").replace("’"," ").replace("-"," ")).split()
def _son(t):
    """forme sonore approximative : homophones et pluriels confondus (aux sprays = au spray, carrure = carure, ajusté = ajustez)"""
    t=''.join(c for c in unicodedata.normalize('NFD',t) if unicodedata.category(c)!='Mn')
    def fin(t):  # terminaisons muettes
        while len(t)>2 and re.search(r'(ent|er|s|x|z|t|d|p|e)$',t):t=re.sub(r'(ent|er|s|x|z|t|d|p|e)$','',t)
        return t
    t=fin(re.sub(r'(.)\1+',r'\1',t))
    for a,b in (('eau','o'),('au','o'),('ai','e'),('ph','f'),('qu','k'),('ce','se'),('ci','si'),('en','an')):t=t.replace(a,b)
    return re.sub(r'(.)\1+',r'\1',fin(t)).replace('c','k')
def _pareil(a,b):
    if all(re.fullmatch(r'[0-9%]+',w) or w in NOMBRES for w in a+b) and any(re.fullmatch(r'[0-9%]+',w) for w in a+b):return True  # « 6 » = « six »
    if len(a)==len(b) and all(_son(x)==_son(y) for x,y in zip(a,b)):return True
    return _son(''.join(a))==_son(''.join(b))  # « micro lésions » = « microlésions »
def _compare(texte,entendu,lg):
    """-> {langue, ecarts:[(attendu, entendu)], score (mots vraiment différents), grave}"""
    a=_mots(texte);b=_mots(entendu);ecarts=[];score=0
    for t,i1,i2,j1,j2 in difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes():
        if t=='equal' or _pareil(a[i1:i2],b[j1:j2]):continue
        ecarts.append((' '.join(a[i1:i2]),' '.join(b[j1:j2])))
        score+=sum(_son(x)!=_son(y) for x,y in zip(a[i1:i2],b[j1:j2])) if i2-i1==j2-j1 else max(i2-i1,j2-j1)
    etranger=lg not in (None,LANGUE) and len(a)>=4
    return {'langue':lg,'ecarts':ecarts,'score':score+(5 if etranger else 0),'grave':etranger or score>=2}
def ecoute(mp3,texte):
    """réécoute d'une seule phrase (une demande Whisper)"""
    from analyse import transcrire
    tr=transcrire(mp3);e=_compare(texte,' '.join(x['text'] for x in tr['segments']),tr.get('language'))
    e['mots']=[{'word':w['word'],'start':round(w['start'],3),'end':round(w['end'],3)} for x in tr['segments'] for w in (x.get('words') or [])];return e
def ecoute_lot(items):
    """Réécoute groupée (13/10/2026) : items=[(id, mp3, texte)] -> {id: résultat de _compare}.
    Toutes les phrases sont collées dans un seul wav (1 s de silence entre deux), une seule demande Whisper (Groq : 20 demandes/min
    au palier gratuit, une par phrase saturait dès deux vidéos en parallèle), puis les mots horodatés sont rendus à leur phrase.
    La langue entendue vaut pour tout le lot (plus de détection d'accent phrase par phrase)."""
    if not items:return {}
    import subprocess,tempfile,wave,numpy as np
    from analyse import transcrire
    SR=16000;GAP=1.0;sil=np.zeros(int(SR*GAP),dtype=np.int16);parts=[sil];pos=GAP;plages=[]
    for i,mp3,t in items:
        raw=subprocess.run([FF,'-v','error','-i',mp3,'-ac','1','-ar',str(SR),'-f','s16le','-'],capture_output=True,check=True).stdout
        a=np.frombuffer(raw,dtype=np.int16);plages.append((i,t,pos,pos+len(a)/SR));parts+=[a,sil];pos+=len(a)/SR+GAP
    tmp=os.path.join(tempfile.gettempdir(),f'ecoute_{os.getpid()}_{abs(hash(tuple(i for i,_,_ in items)))}.wav')
    with wave.open(tmp,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(SR);w.writeframes(np.concatenate(parts).tobytes())
    try:tr=transcrire(tmp)
    finally:
        if os.path.exists(tmp):os.remove(tmp)
    mots=[w for s in tr['segments'] for w in (s.get('words') or [])];lg=tr.get('language')
    if not mots:  # pas d'horodatage mot à mot : on retombe sur une demande par phrase
        return {i:ecoute(mp3,t) for i,mp3,t in items}
    res={}
    for i,t,d0,d1 in plages:
        ws=[w for w in mots if d0-0.3<=(w['start']+w['end'])/2<d1+0.3]
        e=_compare(t,' '.join(w['word'] for w in ws),lg)
        e['mots']=[{'word':w['word'],'start':round(max(0.0,w['start']-d0),3),'end':round(max(0.0,w['end']-d0),3)} for w in ws];res[i]=e
    return res

def cloner(dossier,f):
    stems=os.path.join(dossier,'dm','htdemucs','full','vocals.wav')
    if not os.path.exists(stems):raise SystemExit("voix isolée absente (lance analyse.py avec demucs)")
    ech=os.path.join(dossier,'echantillon_voix.mp3')
    subprocess.run([FF,'-v','error','-y','-ss','0','-t','25','-i',stems,'-ac','1','-b:a','128k',ech],check=True)
    nom=f"{f.get('ref','video')} {f.get('marque','')}".strip()+" clone"  # règle de voix.md : <REF> <marque> clone
    r0=appel(f"{API}/model?self=true&page_size=100&title="+urllib.parse.quote(nom),methode='GET')
    if _code(r0)==200:
        for x in json.load(r0).get('items',[]):
            if x.get('title')==nom and x.get('state')!='failed':print('clone déjà existant, réutilisé :',x['_id']);return x['_id']
    b='----nova';data=open(ech,'rb').read()
    champs=[('type','tts'),('title',nom),('train_mode','fast'),('visibility','private'),('enhance_audio_quality','true')]
    corps=b''.join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k,v in champs)
    corps+=f'--{b}\r\nContent-Disposition: form-data; name="voices"; filename="echantillon.mp3"\r\nContent-Type: audio/mpeg\r\n\r\n'.encode()+data+f'\r\n--{b}--\r\n'.encode()
    r=appel(f"{API}/model",corps,entetes={'Content-Type':f'multipart/form-data; boundary={b}'})
    code=_code(r);rep=r.read().decode('utf-8','ignore')
    if code not in (200,201):
        print(f"CLONAGE REFUSÉ (HTTP {code}) :",rep[:250]);return None
    vid=json.loads(rep)['_id']
    c,t=synthese(vid,'Test.')
    if c!=200:
        print(f"CLONE INUTILISABLE à la synthèse (HTTP {c}) :",str(t)[:200]);print("-> voix de repli (voix.repli_id).")
        supprimer(vid);return None
    print('clone OK :',vid);return vid

def supprimer(vid):
    r=appel(f"{API}/model/{vid}",methode='DELETE');c=_code(r)
    print('clone supprimé :' if c in (200,204) else f'suppression impossible (HTTP {c}) :',vid);return c in (200,204)

def main():
    args=[a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:raise SystemExit(__doc__)
    d=args[0];f=fiche(d);v=f.get('voix',{})
    global LANGUE,MODELE;LANGUE=f.get('langue_cible','fr');MODELE=v.get('modele',MODELE)
    if '--supprimer' in sys.argv:
        if not v.get('id'):raise SystemExit("pas de voix dans la fiche")
        if v.get('id')==v.get('repli_id'):raise SystemExit("la voix de la fiche est la voix de repli (bibliothèque) : on ne la supprime jamais")
        supprimer(v['id']);return
    if '--cloner' in sys.argv:
        vid=cloner(d,f)
        v['id']=vid or v.get('repli_id');f['voix']=v;sauve_fiche(d,f)
        if not v['id']:raise SystemExit("aucune voix : renseigne voix.repli_id dans la fiche")
        print('voix retenue :',v['id']);return
    if not v.get('id'):raise SystemExit("voix.id absent de la fiche (lance --cloner ou renseigne une voix)")
    os.makedirs(os.path.join(d,'tts'),exist_ok=True);vmax=float(v.get('vitesse_max',1.07))
    ids=[a for a in args[1:]] or [l['id'] for l in f['lignes']];force='--force' in sys.argv
    tot=0;trop=[];reprendre='--reprendre' in sys.argv;stab=float(v.get('stabilite',0.5));essais=int(v.get('reprises',2));a_ecouter=[]
    print(f"{'id':5s} {'car':>4s} {'durée':>6s} {'fenêtre':>8s} {'ratio':>6s}  écoute")
    L=f['lignes'];etat=[]
    def prise(k,l,sp):
        avant=L[k-1]['texte'] if k>0 else None;apres=L[k+1]['texte'] if k+1<len(L) else None
        c,r=synthese(v['id'],l['texte'],sp,avant,apres,stab)
        if c!=200:raise SystemExit(f"{l['id']} : Fish Audio HTTP {c} {r}")
        return r,None  # alignement reconstruit après la réécoute
    # 1. synthèse (une demande Fish Audio par phrase à générer)
    for k,l in enumerate(L):
        if l['id'] not in ids:continue
        sp=min(float(l.get('vitesse',1.0)),vmax);cle=hashlib.md5((f"{v['id']}|{sp}|{l['texte']}"+('' if LANGUE=='fr' else '|'+LANGUE)).encode()).hexdigest()
        mp3=os.path.join(d,'tts',l['id']+'.mp3');js=os.path.join(d,'tts',l['id']+'.json');ct=os.path.join(d,'tts',l['id']+'.cle');ec=os.path.join(d,'tts',l['id']+'.ecoute.json')
        neuf=''
        if not force and os.path.exists(mp3) and os.path.exists(js) and os.path.exists(ct) and open(ct).read()==cle:al=json.load(open(js))
        else:
            au,al=prise(k,l,sp);open(mp3,'wb').write(au);open(ct,'w').write(cle);tot+=len(l['texte']);neuf='*'
            if os.path.exists(js):os.remove(js)
        e=json.load(open(ec)) if os.path.exists(ec) else None
        if not e or e.get('cle')!=cle or neuf or al is None:e=None
        etat.append(dict(k=k,l=l,sp=sp,cle=cle,mp3=mp3,js=js,ec=ec,neuf=neuf,al=al,e=e,n=0))
    # 2. réécoute groupée : une seule demande Whisper pour toutes les phrases à vérifier
    a_verifier=[s for s in etat if s['e'] is None]
    res=ecoute_lot([(s['l']['id'],s['mp3'],s['l']['texte']) for s in a_verifier])
    for s in a_verifier:
        e=res[s['l']['id']];e['cle']=s['cle'];s['e']=e;json.dump(e,open(s['ec'],'w'),ensure_ascii=False)
        if s['al'] is None:s['al']=alignement(s['l']['texte'],e.get('mots'),duree_mp3(s['mp3']));json.dump(s['al'],open(s['js'],'w'))
    # 3. reprises : les phrases MAUVAISES sont regénérées puis réécoutées ensemble (une demande Whisper par tour, 2 tours au plus)
    for tour in range(essais):
        cand=[s for s in etat if s['e']['grave'] and (s['neuf'] or reprendre)]
        if not cand:break
        for s in cand:
            au,al2=prise(s['k'],s['l'],s['sp']);tot+=len(s['l']['texte']);s['n']+=1;s['tmp']=s['mp3']+'.essai.mp3';s['al2']=al2;open(s['tmp'],'wb').write(au)
        res=ecoute_lot([(s['l']['id'],s['tmp'],s['l']['texte']) for s in cand])
        for s in cand:  # on garde la meilleure prise
            e2=res[s['l']['id']];e2['cle']=s['cle'];s['al2']=alignement(s['l']['texte'],e2.get('mots'),duree_mp3(s['tmp']))
            if e2['score']<s['e']['score']:os.replace(s['tmp'],s['mp3']);json.dump(s['al2'],open(s['js'],'w'));s['al']=s['al2'];s['e']=e2;json.dump(e2,open(s['ec'],'w'),ensure_ascii=False)
            else:os.remove(s['tmp'])
    # 4. tableau
    for s in etat:
        l,e,al,n,neuf=s['l'],s['e'],s['al'],s['n'],s['neuf']
        dur=al['character_end_times_seconds'][-1];fen=l['fin_fenetre']-l['debut'];ratio=dur/fen
        flag='  TROP LONG' if ratio>1.10 else ('  à écouter' if ratio>1.02 else ('  court' if ratio<0.7 else ''))  # seuils de voix.md
        if ratio>1.10:trop.append(l['id'])
        ver='ok' if not e['ecarts'] and e['langue']==LANGUE else ((('ACCENT '+str(e['langue'])+' ') if e['langue']!=LANGUE else '')+('MAUVAISE ' if e['grave'] else 'à écouter ')+str(e['ecarts'][:3]))
        if e['ecarts'] or e['langue']!=LANGUE:a_ecouter.append(l['id'])
        print(f"{l['id']:5s} {len(l['texte']):4d} {dur:5.2f}s {fen:7.2f}s {ratio:6.2f}{neuf}{flag}  {ver}"+(f"  ({n} reprise(s))" if n else ""))
    print(f"caractères facturés : {tot}  (* = généré maintenant)  trop longs : {trop or 'aucun'}  à écouter : {a_ecouter or 'aucune'}")
    if any(json.load(open(os.path.join(d,'tts',i+'.ecoute.json')))['grave'] for i in a_ecouter):
        print("Phrase(s) MAUVAISE(S) : reformuler (mot plus courant, phrase plus simple), ou --reprendre. Accent persistant : voix de la bibliothèque (voix.md).")
if __name__=='__main__':main()
