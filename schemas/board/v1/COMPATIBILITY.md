# Board command compatibility

| Capability | Command kind | TutorBoard reader | Server reader |
| --- | --- | --- | --- |
| Base board editing | Existing `core.*` command set | 0.1.0+ | board/v1 |
| Atomic Smart Ink acceptance | `core.objects.replace` | This release+ | board/v1 with replace support |
| Semantic 3D solids | `core.solid-3d.*` | BoardDocument 1.3+ | board/v1.3 reader |
| 3D learning attempts | `core.solid-3d-learning.*` | BoardDocument 1.4+ | board/v1.4 reader |

`core.objects.replace` carries complete original and replacement snapshots.
Older strict readers reject this command explicitly. Deployments using server
sync must update the board/v1 reader before enabling Smart Ink for shared
boards.
