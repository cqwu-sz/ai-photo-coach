import json, struct, sys
path = sys.argv[1] if len(sys.argv) > 1 else 'web/avatars/preset/female_youth_18.glb'
with open(path,'rb') as f:
    magic=f.read(4); ver=f.read(4); total=f.read(4)
    cl=struct.unpack('<I',f.read(4))[0]; ct=f.read(4)
    data=f.read(cl)
j=json.loads(data)
nodes=[n.get('name','?') for n in j.get('nodes',[])]
print('file:', path)
print('node count', len(nodes))
print('animations:', [a.get('name') for a in j.get('animations',[])])
print('skins:', len(j.get('skins',[])))
print('--- all node names ---')
for i,n in enumerate(nodes):
    print(i, n)
