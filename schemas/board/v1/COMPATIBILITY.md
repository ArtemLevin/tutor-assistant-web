# Board command compatibility

| Capability | Command kind | TutorBoard reader | Server reader |
| --- | --- | --- | --- |
| Base board editing | Existing `core.*` command set | 0.1.0+ | board/v1 |
| Atomic Smart Ink acceptance | `core.objects.replace` | This release+ | board/v1 with replace support |

`core.objects.replace` carries complete original and replacement snapshots.
Older strict readers reject this command explicitly. Deployments using server
sync must update the board/v1 reader before enabling Smart Ink for shared
boards.
