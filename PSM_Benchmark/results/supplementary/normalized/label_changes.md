# Labels changed by semantic normalization

For each protocol: the states renamed and the transitions relabelled to the ground truth's wording, each with the judge's confidence and reason. Only elements the judge matched by meaning (confidence >= 0.6) are changed; nothing is added or removed. The original exports are untouched; the normalized copies live beside this file.

## TCP — 6 state(s) renamed, 9 transition(s) relabelled

| our state | ground-truth name | conf. | judge reason |
|---|---|---|---|
| SYN_RECEIVED | SYN_RCVD | 1.0 | SYN_RCVD and SYN_RECEIVED are the same state, just different abbreviation. |
| ESTABLISHED | ESTAB | 1.0 | ESTAB is abbreviation for ESTABLISHED. |
| FIN_WAIT_1 | FIN_WAIT-1 | 1.0 | Same state, different punctuation. |
| FIN_WAIT_2 | FIN_WAIT-2 | 1.0 | Same state, different punctuation. |
| TIME_WAIT | TIME-WAIT | 1.0 | Same state, different punctuation. |
| LAST_ACK | LAST-ACK | 1.0 | Same state, different punctuation. |

| edge (after state renames) | our label (event / action) | ground-truth label (event / action) | conf. | judge reason |
|---|---|---|---|---|
| LISTEN → SYN_RECEIVED | receive SYN / send SYN_ACK | receive SYN / send SYN, ACK | 1.0 | Same transition, SYN_ACK = SYN, ACK. |
| LISTEN → SYN_SENT | SEND / send SYN | send SYN / send SYN | 0.8 | SEND user call triggering SYN send from LISTEN corresponds to the 'send SYN' event in GT. |
| SYN_SENT → SYN_RECEIVED | receive SYN / send SYN_ACK | receive SYN / send SYN, ACK | 1.0 | Same transition, simultaneous open case. |
| SYN_SENT → ESTABLISHED | receive SYN ACK / send ACK | receive SYN, ACK / send ACK | 1.0 | Same transition. |
| SYN_RECEIVED → ESTABLISHED | receive ACK / set SND.WND from segment | receive ACK of SYN /  | 0.9 | Receive ACK in SYN_RECEIVED transitions to ESTABLISHED; the ACK is the ACK of SYN. Extra action deta |
| SYN_RECEIVED → LISTEN | receive RST / flush retransmission queue | rcv RST (note1) /  | 0.95 | RST received in SYN_RECEIVED returning to LISTEN matches the note1 case (passive open originated). |
| ESTABLISHED → CLOSE_WAIT | receive FIN / send ACK; signal connection closing | receive FIN / send ACK | 1.0 | Same transition, extra signaling action is fine. |
| FIN_WAIT_2 → TIME_WAIT | receive FIN / send ACK; start time_wait timer | receive FIN / send ACK | 1.0 | Same transition, extra timer start detail. |
| TIME_WAIT → CLOSED | timeout 2MSL / delete TCB | Timeout=2MSL / delete TCB | 1.0 | Same transition. |

## DCCP — 0 state(s) renamed, 14 transition(s) relabelled

| edge (after state renames) | our label (event / action) | ground-truth label (event / action) | conf. | judge reason |
|---|---|---|---|---|
| CLOSED → REQUEST | active open / send DCCP-Request | active open / Send REQUEST | 1.0 | Exact match: active open triggers sending Request, transition to REQUEST. |
| LISTEN → RESPOND | receive DCCP-Request / send DCCP-Response | receive REQUEST / send RESPONSE | 1.0 | Same event and action with DCCP- prefix. |
| RESPOND → OPEN | receive Ack /  | receive ACK or DATAACK /  | 0.95 | Receive Ack matches receive ACK or DATAACK (Ack subsumes the primary case). |
| RESPOND → CLOSED | timeout 4MSL /  | timeout / timeout | 0.85 | Both are timeout transitions from RESPOND to CLOSED; candidate specifies 4MSL. |
| REQUEST → PARTOPEN | receive DCCP-Response / send DCCP-Ack | receive RESPONSE / send ACK | 1.0 | Exact semantic match. |
| PARTOPEN → OPEN | receive valid packet /  | receive packet / send ACK! | 0.85 | Both transition from PARTOPEN to OPEN on receiving a valid packet. Candidate omits the send ACK acti |
| PARTOPEN → CLOSING | receive DCCP-CloseReq / send DCCP-Close | receive CLOSEREQ? / send CLOSE! | 1.0 | Exact semantic match. |
| PARTOPEN → CLOSED | timeout 4MSL / send DCCP-Reset | timeout / timeout | 0.8 | Both are timeout from PARTOPEN to CLOSED. Candidate specifies 4MSL and sending Reset. |
| OPEN → CLOSEREQ | server active close / send DCCP-CloseReq | active close / send CLOSEREQ | 1.0 | Server active close sending CloseReq, exact match. |
| OPEN → CLOSING | active close / send DCCP-Close | active close / send close | 1.0 | Client active close sending Close, exact match. |
| OPEN → CLOSING | receive DCCP-CloseReq / send DCCP-Close | receive closeReq / send close | 1.0 | Exact semantic match. |
| OPEN → CLOSED | receive DCCP-Close / send DCCP-Reset | receive CLOSE / send RESET | 1.0 | Exact semantic match. |
| CLOSEREQ → CLOSED | receive DCCP-Close / send DCCP-Reset | receive CLOSE / send RESET? | 1.0 | Exact semantic match. |
| CLOSING → TIMEWAIT | receive DCCP-Reset /  | receive Reset /  | 1.0 | Exact semantic match. |

## BGP — 0 state(s) renamed, 20 transition(s) relabelled

| edge (after state renames) | our label (event / action) | ground-truth label (event / action) | conf. | judge reason |
|---|---|---|---|---|
| Idle → Connect | ManualStart / initialize BGP resources; set ConnectRetryCounter to zero; start ConnectRetryTimer; initiate TCP connection; listen for TCP connection | ManualStart / Initiate TCP, start ConnectRetryTimer | 1.0 | Same states, same event, same core action of initiating TCP and starting timer |
| Idle → Active | ManualStart PassiveTcp / initialize BGP resources; set ConnectRetryCounter to zero; start ConnectRetryTimer; listen for TCP connection | ManualStart_with_PassiveTcpEstablishment / Start listening, reset ConnectRetryCounter | 1.0 | Same states, same event (passive TCP manual start), same action of listening |
| Idle → Active | AutomaticStart PassiveTcp / initialize BGP resources; set ConnectRetryCounter to zero; start ConnectRetryTimer; listen for TCP connection | AutomaticStart_with_PassiveTcpEstablishment / Start listening, reset ConnectRetryCounter | 1.0 | Same states, same event (passive TCP automatic start), same action of listening |
| Connect → Active | TcpConnectionFails / restart ConnectRetryTimer; stop DelayOpenTimer; listen for TCP connection | TcpConnectionFails / Reset ConnectRetryTimer, listen for incoming connection | 1.0 | Same states, same event, action includes restart ConnectRetryTimer and listen |
| Connect → OpenSent | TcpConnectionConfirmed / stop ConnectRetryTimer; complete BGP initialization; send OPEN message; set HoldTimer large value | TcpConnectionConfirmed / Send OPEN, start HoldTimer | 0.95 | Same states, same event, action sends OPEN and sets HoldTimer |
| Connect → OpenSent | DelayOpenTimer Expires / send OPEN message; set HoldTimer large value | DelayOpenTimer_Expires / Send OPEN, start HoldTimer | 1.0 | Same states, same event, action sends OPEN and sets HoldTimer |
| Active → Idle | TcpConnectionFails / restart ConnectRetryTimer; stop DelayOpenTimer; release all BGP resources; increment ConnectRetryCounter | TcpConnectionFails / Reset ConnectRetryCounter, release resources | 1.0 | Same states, same event, action releases resources |
| Active → OpenSent | DelayOpenTimer Expires / set ConnectRetryTimer to zero; stop DelayOpenTimer; complete BGP initialization; send OPEN message; set HoldTimer large value | DelayOpenTimer_Expires / Send OPEN, start HoldTimer | 1.0 | Same states, same event, action sends OPEN |
| OpenSent → OpenConfirm | BGPOpen / reset DelayOpenTimer to zero; set ConnectRetryTimer to zero; send KEEPALIVE message; set KeepaliveTimer; set HoldTimer negotiated value | BGPOpen / Send KEEPALIVE, start HoldTimer | 1.0 | Same states, same event, action sends KEEPALIVE and sets HoldTimer |
| OpenSent → Idle | BGPOpenMsgErr / send NOTIFICATION with error code; set ConnectRetryTimer to zero; release all BGP resources; drop TCP connection; increment ConnectRetryCounter | BGPOpenMsgErr / Send NOTIFICATION, reset ConnectRetryCounter, release resources | 1.0 | Same states, same event, action sends NOTIFICATION |
| OpenSent → Idle | OpenCollisionDump / send NOTIFICATION with Cease; set ConnectRetryTimer to zero; release all BGP resources; drop TCP connection; increment ConnectRetryCounter | OpenCollisionDump / Send NOTIFICATION, reset ConnectRetryCounter, release resources | 1.0 | Same states, same event, action sends NOTIFICATION with Cease |
| OpenConfirm → Idle | OpenCollisionDump / send NOTIFICATION with Cease; set ConnectRetryTimer to zero; release all BGP resources; drop TCP connection; increment ConnectRetryCounter | KeepAliveMsg / Restart HoldTimer | 1.0 | Same states, same event, action restarts HoldTimer |
| OpenConfirm → Idle | HoldTimer Expires / send NOTIFICATION HoldTimer Expired; set ConnectRetryTimer to zero; release all BGP resources; drop TCP connection; increment ConnectRetryCounter | HoldTimer_Expires / Send NOTIFICATION, reset ConnectRetryCounter, release resources | 1.0 | Same states, same event, action sends NOTIFICATION |
| OpenConfirm → Idle | NotifMsg / set ConnectRetryTimer to zero; release all BGP resources; drop TCP connection; increment ConnectRetryCounter | NotifMsgVerErr / Send NOTIFICATION, reset ConnectRetryCounter, release resources | 1.0 | Same states, same event, action releases resources |
| Established → Idle | ManualStop / send NOTIFICATION with Cease; set ConnectRetryTimer to zero; delete associated routes; release all BGP resources; drop TCP connection; set ConnectRetryCounter to zero | ManualStop / Send NOTIFICATION, reset ConnectRetryCounter, release resources | 1.0 | Same states, same event, action sends NOTIFICATION with Cease |
| Established → Idle | AutomaticStop / send NOTIFICATION with Cease; set ConnectRetryTimer to zero; delete associated routes; release all BGP resources; drop TCP connection; increment ConnectRetryCounter | AutomaticStop / Send NOTIFICATION, reset ConnectRetryCounter, release resources | 1.0 | Same states, same event, action sends NOTIFICATION with Cease |
| Established → Idle | HoldTimer Expires / send NOTIFICATION HoldTimer Expired; set ConnectRetryTimer to zero; release all BGP resources; drop TCP connection; increment ConnectRetryCounter | HoldTimer_Expires / Send NOTIFICATION, reset ConnectRetryCounter, release resources | 1.0 | Same states, same event, action sends NOTIFICATION |
| Established → Idle | NotifMsgVerErr / set ConnectRetryTimer to zero; delete associated routes; release all BGP resources; drop TCP connection; increment ConnectRetryCounter | NotifMsgVerErr / Send NOTIFICATION, reset ConnectRetryCounter, release resources | 1.0 | Same states, same event, action releases resources |
| Established → Idle | TcpConnectionFails / set ConnectRetryTimer to zero; delete associated routes; release all BGP resources; drop TCP connection; increment ConnectRetryCounter | TcpConnectionFails / Reset ConnectRetryCounter, release resources | 1.0 | Same states, same event, action releases resources |
| Established → Idle | UpdateMsgErr / send NOTIFICATION Update error; set ConnectRetryTimer to zero; delete associated routes; release all BGP resources; drop TCP connection; increment ConnectRetryCounter | UpdateMsgErr / Send NOTIFICATION, reset ConnectRetryCounter, release resources | 1.0 | Same states, same event, action sends NOTIFICATION |

## PPP — 0 state(s) renamed, 26 transition(s) relabelled

| edge (after state renames) | our label (event / action) | ground-truth label (event / action) | conf. | judge reason |
|---|---|---|---|---|
| Initial → Closed | Up /  | receive lower layer up (Up) / send This-Layer-Up (tlu) | 0.8 | Same states and event Up. GT says tlu action but candidate omits it; the RFC table for Initial/Up ac |
| Initial → Starting | Open / signal this layer started | receive administrative Open (Open) / send This-Layer-Started (tls) | 1.0 | Same states, event Open, action tls |
| Starting → ReqSent | Up / initialize restart count; send Configure-Request | receive lower layer up (Up) / send Initialize-Restart-Count (irc), send Configure-Request (scr) | 1.0 | Exact match |
| Closed → Initial | Down /  | receive lower layer down (Down) / send This-Layer-Finished (tlf) | 0.8 | Same states and event. Candidate omits tlf but RFC table for Closed/Down has no tlf, so same transit |
| Closed → ReqSent | Open / initialize restart count; send Configure-Request | receive administrative Open (Open) / send Initialize-Restart-Count (irc), send Configure-Request (scr) | 1.0 | Exact match |
| Closed → Closed | Close /  | receive administrative Close (Close) /  | 1.0 | Exact match |
| Stopped → Starting | Down / signal this layer started | receive lower layer up (Up) / send This-Layer-Started (tls) | 0.7 | GT says Up but candidate says Down. RFC table actually has Down->Starting/tls for Stopped. The GT ev |
| Stopped → Closed | Close /  | receive administrative Close (Close) / send This-Layer-Finished (tlf) | 0.8 | Same states and event Close. Candidate omits tlf but same transition. |
| ReqSent → Stopped | timeout TO minus / signal this layer finished | receive administrative Close (Close) / send Terminate-Request (str), send Initialize-Restart-Count (irc) | 1.0 | Exact match |
| ReqSent → AckSent | receive RCR plus / send Configure-Ack | timeout counter > 0 (TO+) / send Configure-Request (scr) | 1.0 | Exact match |
| ReqSent → ReqSent | receive RCR minus / send Configure-Nak | timeout counter expired (TO-) / send This-Layer-Finished (tlf) | 1.0 | Exact match |
| ReqSent → ReqSent | receive RTR / send Terminate-Ack | receive Configure-Ack (RCA) / send Initialize-Restart-Count (irc) | 1.0 | Exact match |
| ReqSent → AckRcvd | receive RCA / initialize restart count | receive Configure-Request (RCR+) / send Configure-Ack (sca) | 1.0 | Exact match |
| AckRcvd → Opened | receive RCR plus / send Configure-Ack; signal this layer up | receive administrative Close (Close) / send Terminate-Request (str), send Initialize-Restart-Count (irc) | 1.0 | Exact match |
| AckRcvd → ReqSent | receive RCN / send Configure-Request | receive Configure-Request (RCR+) / send Configure-Ack (sca), send This-Layer-Up (tlu) | 1.0 | Exact match |
| AckRcvd → AckRcvd | receive RUC / send Code-Reject | receive Configure-Nak/Rej (RCN) / send Configure-Request (scr) | 1.0 | Exact match |
| AckSent → Stopped | timeout TO minus / signal this layer finished | receive administrative Close (Close) / send Terminate-Request (str), send Initialize-Restart-Count (irc) | 1.0 | Exact match |
| AckSent → ReqSent | receive RTR / send Terminate-Ack | receive Configure-Ack (RCA) / send This-Layer-Up (tlu) | 1.0 | Same transition; candidate adds irc which is per RFC |
| AckSent → AckSent | receive RCN / initialize restart count; send Configure-Request | receive Configure-Request (RCR-) / send Configure-Request (scr) | 0.8 | Same states and event RCR-. Action differs (scr vs scn) but both are valid parts of the response to  |
| Opened → AckSent | receive RCR plus / signal this layer down; send Configure-Request; send Configure-Ack | receive administrative Close (Close) / send Terminate-Request (str), send Initialize-Restart-Count (irc) | 1.0 | Same transition; candidate adds tld which is per RFC |
| Opened → ReqSent | receive RTA / signal this layer down; send Configure-Request | receive Terminate-Request (RTR) / send Terminate-Request (str) | 0.85 | Same states and event RTR. GT says send str but RFC says send Terminate-Ack; candidate is more corre |
| Stopping → Stopping | receive RXJ plus /  | receive Terminate-Ack (RTA) / send This-Layer-Finished (tlf) | 1.0 | Exact match |
| Stopping → Stopping | receive RCR minus /  | timeout counter > 0 (TO+) / send Terminate-Request (str) | 1.0 | Exact match |
| Stopping → Stopping | receive RCA /  | timeout counter expired (TO-) / send This-Layer-Finished (tlf) | 1.0 | Exact match |
| Closing → Closing | receive RUC / send Code-Reject | receive Terminate-Ack (RTA) / send This-Layer-Finished (tlf) | 1.0 | Exact match |
| Closing → Closed | timeout TO minus / signal this layer finished | timeout counter expired (TO-) / send This-Layer-Finished (tlf) | 1.0 | Exact match |

## DHCP — 1 state(s) renamed, 15 transition(s) relabelled

| our state | ground-truth name | conf. | judge reason |
|---|---|---|---|
| INIT_REBOOT | INIT-REBOOT | 1.0 | Same state, just hyphen vs underscore naming difference. |

| edge (after state renames) | our label (event / action) | ground-truth label (event / action) | conf. | judge reason |
|---|---|---|---|---|
| INIT → SELECTING | send DHCPDISCOVER / broadcast DHCPDISCOVER |  / Send DHCPDISCOVER | 1.0 | Both transition from INIT to SELECTING by sending DHCPDISCOVER. |
| SELECTING → REQUESTING | select offer / select server from offers; send DHCPREQUEST | Select offer / Send DHCPREQUEST | 1.0 | Both select an offer and send DHCPREQUEST, transitioning SELECTING->REQUESTING. |
| SELECTING → SELECTING | receive DHCPOFFER / collect replies | receive DHCPOFFER / Collect replies | 1.0 | Identical: receive DHCPOFFER, collect replies, stay in SELECTING. |
| REQUESTING → BOUND | receive DHCPACK / record lease; set timer T1; set timer T2 | receive DHCPACK / Record lease Set timers | 1.0 | Both receive DHCPACK in REQUESTING, record lease, set timers, go to BOUND. |
| REQUESTING → INIT | receive DHCPNAK / discard offer; restart configuration | receive DHCPNAK / Discard offer | 1.0 | Both receive DHCPNAK in REQUESTING, discard offer, go to INIT. |
| REQUESTING → REQUESTING | receive DHCPOFFER / discard | receive DHCPOFFER / Discard | 1.0 | Identical: discard DHCPOFFER while in REQUESTING. |
| INIT_REBOOT → REBOOTING | send DHCPREQUEST / send DHCPREQUEST |  / Send DHCPREQUEST | 1.0 | Both transition from INIT-REBOOT to REBOOTING by sending DHCPREQUEST. |
| REBOOTING → BOUND | receive DHCPACK / record lease; set timer T1; set timer T2 | receive DHCPACK / Record lease Set timers T1 T2 | 1.0 | Both receive DHCPACK in REBOOTING, record lease, set timers, go to BOUND. |
| REBOOTING → INIT | receive DHCPNAK / restart | receive DHCPNAK / Restart | 1.0 | Identical: receive DHCPNAK in REBOOTING, restart, go to INIT. |
| BOUND → RENEWING | timeout T1 expires / send DHCPREQUEST to server | timer T1 expires / Send DHCPREQUEST | 1.0 | Both: T1 expires in BOUND, send DHCPREQUEST, go to RENEWING. |
| BOUND → BOUND | receive unsolicited msg / discard | receive DHCPOFFER / Discard | 0.6 | Candidate uses 'unsolicited msg' which encompasses DHCPOFFER, DHCPACK, DHCPNAK. This is a generalize |
| RENEWING → BOUND | receive DHCPACK / record lease; set timer T1; set timer T2 | Receive DHCPACK / Record lease; Set timers T1, T2 | 1.0 | Identical: receive DHCPACK in RENEWING, record lease, set timers, go to BOUND. |
| RENEWING → REBINDING | timeout T2 expires / broadcast DHCPREQUEST | timer T2 expires / send Broadcast DHCPREQUEST | 1.0 | Both: T2 expires in RENEWING, broadcast DHCPREQUEST, go to REBINDING. |
| REBINDING → BOUND | receive DHCPACK / record lease; set timer T1; set timer T2 | receive DHCPACK / Record lease; Set timers T1, T2 | 1.0 | Identical: receive DHCPACK in REBINDING, record lease, set timers, go to BOUND. |
| REBINDING → INIT | receive DHCPNAK / halt network | receive DHCPNAK / Halt network | 1.0 | Identical: receive DHCPNAK in REBINDING, halt network, go to INIT. |

## PPTP — 8 state(s) renamed, 11 transition(s) relabelled

| our state | ground-truth name | conf. | judge reason |
|---|---|---|---|
| idle | Idle | 1.0 | Both are the initial resting state |
| wait_ctl_reply | Wait Control Reply | 1.0 | Both wait for Start-Control-Connection-Reply |
| established | Established | 1.0 | Both represent active connection |
| wait_stop_reply | Wait Stop Reply | 1.0 | Both wait for Stop-Control-Connection-Reply |
| wait_connect | Wait Connect | 1.0 | Both wait for Incoming-Call-Connected |
| wait_disconnect | Wait Disconnect | 1.0 | Both wait for Call-Disconnect-Notify after Call-Clear-Request |
| collision | Collision Handling | 0.6 | Both represent collision handling, though candidate never transitions into collision state |
| wait_reply | Wait Outgoing Reply | 0.7 | Candidate wait_reply used for outgoing call reply waiting among others |

| edge (after state renames) | our label (event / action) | ground-truth label (event / action) | conf. | judge reason |
|---|---|---|---|---|
| wait_disconnect → idle | receive CallDisconnectNotify /  | Local Terminate / Send Call Clear Request | 1.0 | Same event, action, and state transition for call-level local terminate |
| idle → wait_ctl_reply | cond TCP established / send StartCtlConnRequest | TCP Open Indication / Send Start Control Connection Request | 1.0 | TCP open triggers sending Start-Control-Connection-Request |
| wait_ctl_reply → established | receive StartCtlReply ok / establish control connection | Receive Start Control Connection Reply (Version OK) / Establish control connection | 1.0 | Exact match on receiving successful reply |
| wait_ctl_reply → wait_stop_reply | receive StartCtlReply unsupported / send StopCtlConnRequest | Receive Start Control Connection Reply (Version Not OK) / Send Stop Control Connection Request | 1.0 | Version mismatch triggers stop request |
| established → wait_stop_reply | cond local terminate / send StopCtlConnRequest | Local Terminate / Send Stop Control Connection Request | 1.0 | Control-level local terminate sends stop request |
| established → idle | receive StopCtlConnRequest / send StopCtlConnReply; close TCP connection | Receive Stop Control Connection Request / Send Stop Control Connection Reply | 0.6 | Same event and action but candidate goes to idle instead of wait_stop_reply; partial match |
| wait_stop_reply → idle | receive StopCtlConnReply / close TCP connection | Receive Stop Control Connection Reply / Close TCP, return to Idle | 1.0 | Exact match |
| wait_connect → established | receive InCallConnected /  | Receive Incoming Call Connected / Confirm session establishment | 1.0 | Same transition: receive connected, move to established |
| wait_reply → idle | receive OutCallReply error /  | Send Outgoing Call Request / Wait for PAC response | 0.9 | PNS sends outgoing call request and waits |
| wait_reply → wait_disconnect | cond abort / send CallClearRequest | Receive Outgoing Call Reply (No Error) / Establish call | 1.0 | Successful outgoing call reply establishes call |
| wait_reply → established | receive OutCallReply ok / connect telco call | Receive Outgoing Call Reply (Error) / Return to Idle | 1.0 | Error reply returns to idle |

## IMAP — 2 state(s) renamed, 11 transition(s) relabelled

| our state | ground-truth name | conf. | judge reason |
|---|---|---|---|
| ServerGreeting | Connection_Established_Server_Greeting | 0.85 | Both represent the state where the server issues its initial greeting (OK, PREAUTH, or BYE). The candidate splits connec |
| NotAuthenticated | Not_Authenticated | 1.0 | Same protocol state: client connected but not yet authenticated. |

| edge (after state renames) | our label (event / action) | ground-truth label (event / action) | conf. | judge reason |
|---|---|---|---|---|
| ServerGreeting → NotAuthenticated | receive OK greeting / send OK greeting | connect without pre-authentication / Send OK greeting | 0.9 | Both transition from the greeting state to NotAuthenticated upon an OK greeting. |
| ServerGreeting → Authenticated | receive PREAUTH greeting / send PREAUTH greeting | Connects with pre-authentication / Send PREAUTH greeting | 0.95 | Both transition from greeting state to Authenticated upon PREAUTH. |
| ServerGreeting → Logout | receive BYE greeting / send BYE greeting | Connection reject / Send BYE greeting | 0.95 | Both transition from greeting state to Logout upon BYE greeting (connection rejected). |
| NotAuthenticated → Authenticated | receive AUTHENTICATE OK / enter Authenticated state | Sends LOGIN or AUTHENTICATE command / Sends LOGIN or AUTHENTICATE command | 0.8 | Both represent successful authentication transitioning to Authenticated. Candidate splits LOGIN and  |
| NotAuthenticated → Logout | send LOGOUT command / send BYE untagged response; send tagged OK response | Sends LOGOUT or server shutdown / Close connection | 0.8 | Both represent logout from NotAuthenticated state. GT combines LOGOUT and server shutdown; this matc |
| Authenticated → Selected | receive SELECT OK / select mailbox for access | Sends SELECT or EXAMINE command / Sends SELECT or EXAMINE command | 0.85 | Both represent successful SELECT transitioning from Authenticated to Selected. |
| Authenticated → Logout | send LOGOUT command / send BYE untagged response; send tagged OK response | Sends LOGOUT or server shutdown / Close connection | 0.8 | Both represent logout from Authenticated. GT combines LOGOUT and server shutdown; this matches LOGOU |
| Authenticated → Authenticated | send LIST command / send untagged LIST responses | Send requests mailbox listing / List mailbox | 0.95 | Both represent listing mailboxes while remaining in Authenticated state. |
| Selected → Authenticated | send CLOSE command / close selected mailbox; send tagged OK response | Sends CLOSE, UNSELECT, or SELECT/EXAMINE fails / Sends CLOSE, UNSELECT, or SELECT/EXAMINE fails | 0.8 | Both represent returning from Selected to Authenticated via CLOSE. GT combines CLOSE/UNSELECT/fail;  |
| Selected → Selected | send FETCH command / send untagged FETCH responses | Client manages messages / Read/write/delete messages | 0.7 | FETCH is one form of message management (reading). GT is a general 'manages messages' self-loop. |
| Selected → Logout | send LOGOUT command / send BYE untagged response; send tagged OK response | Sends LOGOUT or server shutdown / Close connection | 0.8 | Both represent logout from Selected state. GT combines LOGOUT and server shutdown. |

## POP3 — 0 state(s) renamed, 13 transition(s) relabelled

| edge (after state renames) | our label (event / action) | ground-truth label (event / action) | conf. | judge reason |
|---|---|---|---|---|
| WaitForConnection → AUTHORIZATION | receive connection / send greeting | send greeting / reply +OK POP3 server ready | 0.6 | Both represent the greeting event that leads to the AUTHORIZATION state. The GT has it as a self-loo |
| AUTHORIZATION → AUTHORIZATION | receive USER / process USER command | receive USER / reply request password | 0.9 | Both handle the USER command in AUTHORIZATION state, staying in AUTHORIZATION. Processing the USER c |
| AUTHORIZATION → TRANSACTION | cond auth success / acquire maildrop lock; assign message numbers; send positive response | cond valid USER/PASS / set authenticated true | 0.85 | Both represent successful authentication transitioning from AUTHORIZATION to TRANSACTION. The candid |
| AUTHORIZATION → TRANSACTION | receive APOP success / send +OK response; enter TRANSACTION state | cond valid APOP / set authenticated true | 0.9 | Both represent successful APOP authentication transitioning to TRANSACTION. |
| AUTHORIZATION → AUTHORIZATION | cond auth rejected lock / release maildrop lock; send -ERR response | cond open maildrop failed / reply -ERR and release lock | 0.8 | Both represent a failure to open/lock the maildrop, sending -ERR and releasing the lock, staying in  |
| AUTHORIZATION → WaitForConnection | receive QUIT / send +OK response; close TCP connection | receive QUIT / reply +OK and close TCP | 0.7 | Both handle QUIT in AUTHORIZATION with +OK and close TCP. Destination differs (UPDATE vs WaitForConn |
| TRANSACTION → TRANSACTION | receive STAT /  | receive STAT / reply message count size | 0.9 | Same event and state transition. Candidate omits action detail but the meaning is the same. |
| TRANSACTION → TRANSACTION | receive LIST /  | receive LIST / reply scan listing | 0.9 | Same event and state transition for LIST command. |
| TRANSACTION → TRANSACTION | receive RETR /  | receive RETR / reply full message | 0.9 | Same event and state transition for RETR command. |
| TRANSACTION → TRANSACTION | receive DELE / mark message as deleted; reply +OK | receive DELE / set message deleted | 1.0 | Same event, same action (mark as deleted), same state transition. |
| TRANSACTION → TRANSACTION | receive NOOP /  | receive NOOP / reply OK | 0.9 | Same event and state transition for NOOP. |
| TRANSACTION → TRANSACTION | receive RSET / unmark all deleted messages; reply +OK | receive RSET / reset deleted flags | 1.0 | Same event, same action (reset/unmark deleted), same state transition. |
| TRANSACTION → UPDATE | receive QUIT / enter UPDATE state | receive QUIT / set session closing | 1.0 | Both handle QUIT in TRANSACTION transitioning to UPDATE. |

## SIP — 0 state(s) renamed, 20 transition(s) relabelled

| edge (after state renames) | our label (event / action) | ground-truth label (event / action) | conf. | judge reason |
|---|---|---|---|---|
| Calling → Calling | timeout TimerA / retransmit INVITE request | timeout Timer A / send INVITE | 1.0 | Same Timer A retransmission of INVITE |
| Calling → Proceeding | receive 1xx / pass response to TU | receive 1xx / send 1xx to TU | 1.0 | Same 1xx handling in Calling |
| Calling → Terminated | receive 2xx / pass 2xx to TU | receive 2xx / send 2xx to TU | 1.0 | Same 2xx handling in Calling |
| Calling → Terminated | timeout TimerB / inform TU of timeout | timeout Timer B or Transport Error / inform TU | 0.8 | Timer B part matches; transport error is separate in candidate (index 5) |
| Calling → Completed | receive 3xx to 6xx / pass response to TU; generate ACK; start TimerD | receive 300-699 / send ACK, send response to TU | 1.0 | Same 3xx-6xx handling with ACK generation |
| Proceeding → Terminated | receive 2xx / pass 2xx to TU | receive 2xx / send 2xx to TU | 1.0 | Same 2xx in Proceeding |
| Proceeding → Completed | receive 3xx to 6xx / pass response to TU; generate ACK; start TimerD | receive 300-699 / send ACK, send response to TU | 1.0 | Same 3xx-6xx handling in Proceeding |
| Proceeding → Proceeding | receive 1xx / pass response to TU | receive 1xx / send 1xx to TU | 1.0 | Same 1xx in Proceeding |
| Proceeding → Proceeding | receive RequestRetransmit / retransmit last response | receive INVITE / send response | 0.75 | INVITE retransmit in Proceeding triggers response retransmission - server INVITE transaction behavio |
| Proceeding → Terminated | cond TransportError / inform TU | receive Transport Error / inform TU | 1.0 | Same transport error handling in Proceeding |
| Completed → Terminated | timeout TimerD / destroy transaction | timeout Timer D /  | 1.0 | Same Timer D expiry |
| Completed → Completed | receive ResponseRetransmit / re-pass ACK to transport | receive 300-699 / send ACK | 0.7 | Response retransmit in Completed triggers ACK retransmission - client INVITE transaction |
| Completed → Terminated | cond TransportError / inform TU | receive Transport Error / inform TU | 1.0 | Same transport error in Completed |
| Completed → Confirmed | receive ACK / set TimerI | receive ACK /  | 1.0 | Same ACK reception moving to Confirmed |
| Confirmed → Terminated | timeout TimerI / destroy transaction | timeout Timer I /  | 1.0 | Same Timer I expiry |
| Proceeding → Proceeding | send Provisional / pass to transport | receive 101-199 from TU / send response | 0.85 | TU sends provisional → transport; server INVITE transaction |
| Proceeding → Completed | send 3xx to 6xx / pass to transport; start TimerG TimerH | receive 300-699 from TU / send response | 0.9 | TU sends final non-2xx response; server INVITE transaction |
| Completed → Completed | timeout TimerG / retransmit response | timeout Timer G / send response | 1.0 | Same Timer G retransmission |
| Completed → Completed | receive RequestRetransmit / retransmit response | receive INVITE / send response | 0.8 | INVITE retransmit in Completed triggers response retransmission - server side |
| Completed → Terminated | timeout TimerH / inform TU | timeout Timer H or Transport Error / inform TU | 0.8 | Timer H part matches; transport error is separate in candidate |

## RTSP — 0 state(s) renamed, 24 transition(s) relabelled

| edge (after state renames) | our label (event / action) | ground-truth label (event / action) | conf. | judge reason |
|---|---|---|---|---|
| Init → Ready | receive SETUP success / generate session identifier; set transport parameters | cond default / send SETUP; reply NRM=1, RP=0.0 | 0.9 | Both represent successful SETUP transitioning Init->Ready |
| Init → Init | receive SETUP redirect / send 3rr redirect response | cond Needs Redirect / send SETUP; reply 3rr Redirect | 0.95 | Both represent SETUP requiring redirect, staying in Init |
| Init → Init | receive REDIRECT / terminate all sessions | cond No Session hdr / send S -> C: REDIRECT; reply Terminate all SES | 0.85 | Both represent REDIRECT terminating all sessions in Init |
| Ready → Ready | receive SETUP / update transport parameters | cond URI Setup prior / send SETUP; reply Change transport param | 0.8 | Both represent SETUP changing transport parameters in Ready |
| Ready → Ready | receive TEARDOWN media / remove media stream; free associated resources | cond md URI,NRM>1 / send TEARDOWN; reply Session hdr, NRM -= 1 | 0.8 | Both represent TEARDOWN of a media URI when NRM>1, staying in Ready |
| Ready → Ready | receive PAUSE / send 200 OK response | cond Prs URI / send PAUSE; reply Return PP | 0.85 | Both represent PAUSE in Ready state |
| Ready → Ready | receive REDIRECT timed / set redirect deadline | cond Terminate-Reason / send SC:REDIRECT; reply Set RedP | 0.9 | Both represent timed REDIRECT setting a redirect point in Ready |
| Ready → Play | receive PLAY / begin media delivery | cond Prs URI, Range / send PLAY; reply According to range | 0.75 | Both represent PLAY transitioning Ready->Play |
| Ready → Init | receive TEARDOWN session / destroy session state | cond Prs URI / send TEARDOWN; reply No session hdr, NRM = 0 | 0.85 | Both represent session-level TEARDOWN from Ready to Init |
| Ready → Init | receive REDIRECT immediate / remove session | cond No Terminate-Reason time parameter / send SC:REDIRECT; reply Session is removed | 0.9 | Both represent immediate REDIRECT removing session |
| Ready → Init | timeout session / remove session | cond default / timeout | 0.95 | Both represent session timeout in Ready going to Init |
| Ready → Init | cond redirect deadline / teardown session | cond default / cond RedP reached; reply TEARDOWN of session | 0.95 | Both represent redirect point reached causing teardown |
| Play → Play | receive PLAY / replace current play action | cond Prs URI, Range / send PLAY; reply According to range | 0.7 | Both represent PLAY in Play state staying in Play |
| Play → Play | cond end of range / stop media delivery | cond default / set Set RP = End of range | 0.75 | Both represent end-of-range condition in Play state |
| Play → Play | cond end of media / set resume point to end | cond All media / set Set RP = End of media | 0.9 | Both represent end-of-media setting resume point |
| Play → Play | receive PLAY NOTIFY / send 200 response | cond default / send SC:PLAY_NOTIFY; reply 200 | 0.85 | Both represent PLAY_NOTIFY with 200 response in Play |
| Play → Play | receive SETUP / change transport parameters | cond md URI, IFI / send SETUP; reply Change transport param. | 0.85 | Both represent SETUP changing transport params in Play |
| Play → Play | receive REDIRECT timed / set redirect deadline | cond Terminate Reason with Time parameter / send SC:REDIRECT; reply Set RedP | 0.9 | Both represent timed REDIRECT in Play state |
| Play → Ready | receive PAUSE / halt media delivery | cond Prs URI / send PAUSE; reply Set RP to present point | 0.9 | Both represent PAUSE transitioning Play->Ready |
| Play → Init | receive TEARDOWN session / destroy session state | cond Prs URI / send TEARDOWN; reply No session hdr | 0.85 | Both represent session TEARDOWN from Play to Init |
| Play → Init | receive REDIRECT immediate / remove session | cond default / send SC:REDIRECT; reply Session is removed | 0.9 | Both represent immediate REDIRECT removing session from Play |
| Play → Init | timeout session / stop media playout | cond default / timeout; reply Stop Media playout | 0.95 | Both represent session timeout stopping media in Play |
| Play → Init | cond redirect deadline / teardown session | cond default / cond RedP reached; reply TEARDOWN of session | 0.95 | Both represent redirect deadline reached causing teardown from Play |
| Play → Init | receive TEARDOWN affected / terminate media session | cond md URI,NRM=1 / send TEARDOWN; reply No Session hdr, NRM=0 | 0.7 | Both represent TEARDOWN of last media URI from Play to Init |

## MQTT — 8 state(s) renamed, 8 transition(s) relabelled

| our state | ground-truth name | conf. | judge reason |
|---|---|---|---|
| NewSession | Disconnected | 0.7 | Both are the initial state before any connection/token activity |
| TokenObtained | Token Received | 0.95 | Both represent having received/obtained the access token from AS |
| WaitingForConnack | Awaiting CONNACK | 0.9 | Both represent waiting for CONNACK from broker |
| Connecting | Authenticating | 0.6 | Both represent the phase where connection request with token is being processed |
| TokenValidation | Token Validating | 0.95 | Both represent broker validating the token |
| ExistingSessionContinued | Session Resumed | 0.85 | Both represent a resumed/continued prior session |
| ValidationFailed | Unauthorized | 0.85 | Both represent failed token validation / unauthorized state |
| Disconnected | Session Terminated | 0.75 | Candidate's Disconnected is a final state representing session end |

| edge (after state renames) | our label (event / action) | ground-truth label (event / action) | conf. | judge reason |
|---|---|---|---|---|
| NewSession → TokenObtained | receive access token / receive access token from AS | Token received from AS / Store JWT/CWT token | 0.7 | Both represent receiving token from AS, though source states differ due to GT having intermediate st |
| TokenObtained → Connecting | send connection request / send connection request with token | Send CONNECT/AUTH packet / Include token in 'authz-info' topic | 0.8 | Both send connection request with token from token-received state to authenticating/connecting state |
| TokenValidation → Connected | cond token valid / send CONNACK reason_code 0x00 | Token valid / Send CONNACK(Success) | 0.95 | Both: token valid → send CONNACK success → Connected |
| TokenValidation → ValidationFailed | cond token expired or invalid / check token expiry; check token audience; check issuer authorization | Token invalid/expired / Send CONNACK(Not Authorized) | 0.8 | Both: invalid/expired token from validation state to unauthorized/failed state |
| CurrentSession → ExistingSessionContinued | receive CONNECT cleanFlag0 / resume communications from current session | Session validated / Send CONNACK(Session Present=1) | 0.85 | Both: resumed session validated → CONNACK SessionPresent=1 → Connected |
| Connected → Reauthenticating | send AUTH reauth / send AUTH 0x19 Reauthentication; set auth method ace; transport new token | Token expired / Send AUTH packet | 0.85 | Both: Connected → send AUTH → Reauthenticating |
| Reauthenticating → Disconnected | cond reauth failed / send DISCONNECT 0x87 | Reauthentication failed / Send DISCONNECT | 0.8 | Both: reauth failed → DISCONNECT. Destination states differ in name but both represent session end |
| Connected → Disconnected | send CLIENT DISCONNECT / discard Session State; keep retained messages | DISCONNECT received/sent / Cleanup session | 0.8 | Both: Connected → DISCONNECT → session terminated/disconnected |

## SMTP — 6 state(s) renamed, 9 transition(s) relabelled

| our state | ground-truth name | conf. | judge reason |
|---|---|---|---|
| Greeting | Connection Established | 0.6 | After TCP connection opened and 220 sent, waiting for EHLO/HELO - maps to the state before greeting exchange completes |
| SessionInitialized | Greeted | 0.95 | Both represent state after successful EHLO/HELO, ready for MAIL FROM |
| MailTransaction | Mail Transaction Started | 0.8 | Both represent state after MAIL FROM accepted; candidate merges RCPT phase too |
| DataTransfer | Data Entry | 0.95 | Both represent the data transfer phase after DATA/354 |
| MailAccepted | Message Accepted | 0.7 | Both represent post-message-acceptance, though candidate focuses on delivery responsibility |
| ConnectionClosed | Session Terminated | 0.95 | Both represent the session/connection being closed |

| edge (after state renames) | our label (event / action) | ground-truth label (event / action) | conf. | judge reason |
|---|---|---|---|---|
| Greeting → SessionInitialized | receive EHLO accepted / clear all state tables and buffers | receive EHLO or HELO after 220 greeting / send HELO | 0.7 | Both transition from initial state to greeted/initialized after EHLO accepted |
| SessionInitialized → MailTransaction | receive MAIL accepted / clear buffers and insert reverse-path | receive MAIL FROM / send MAIL FROM | 0.85 | Both transition from greeted to mail transaction on MAIL command |
| MailTransaction → DataTransfer | receive DATA accepted / send 354 intermediate reply | receive DATA / send DATA 354 | 0.75 | Both transition to data transfer on DATA command with 354 reply; source states differ but semantical |
| DataTransfer → MailAccepted | send 250 data accepted / accept delivery responsibility | cond message ended with <CRLF>.<CRLF> / send message body | 0.65 | Both represent successful completion of data transfer leading to message accepted |
| SessionInitialized → SessionInitialized | receive RSET / send 250 OK reply | receive RSET / send RSET | 0.9 | Both are RSET self-loop in greeted/initialized state |
| MailTransaction → SessionInitialized | receive RSET / abort transaction and clear buffers | receive RSET / send RSET | 0.9 | Both reset from mail transaction back to greeted/initialized |
| Greeting → ConnectionClosed | receive QUIT / send 221 and close connection | receive QUIT / send QUIT | 0.7 | Both handle QUIT from initial/greeting state to closed |
| SessionInitialized → ConnectionClosed | receive QUIT / send 221 and close connection | receive QUIT / send QUIT | 0.9 | Both handle QUIT from greeted/initialized to closed |
| MailTransaction → ConnectionClosed | receive QUIT / abort current transaction | receive QUIT / send QUIT | 0.85 | Both handle QUIT from mail transaction to closed |

## NNTP — 6 state(s) renamed, 6 transition(s) relabelled

| our state | ground-truth name | conf. | judge reason |
|---|---|---|---|
| Established | Initial_Connection | 0.95 | Both represent the initial state upon TCP connection before greeting is processed. |
| Closed | Connection_Closed | 1.0 | Both represent the terminal closed connection state. |
| PostingAllowed | Posting_Allowed | 0.75 | Both represent a state where posting is allowed. In GT it's reached after MODE READER; in candidate it's reached directl |
| PostingProhibited | Posting_Prohibited | 0.85 | Both represent a state where posting is prohibited, reached after 201 greeting. |
| GroupSelected | Group_Selected | 0.95 | Both represent the state where a newsgroup has been selected. |
| ArticleInput | Posting_Article | 0.85 | Both represent the state where an article is being submitted/posted, awaiting the result (240 or 441). |

| edge (after state renames) | our label (event / action) | ground-truth label (event / action) | conf. | judge reason |
|---|---|---|---|---|
| Established → PostingAllowed | receive 200 greeting / send 200 response | Receive 200 / Set service available | 0.6 | Both transition from initial state on receiving 200 greeting. Destination states differ in naming bu |
| Established → PostingProhibited | receive 201 greeting / send 201 response | Receive 201 / Set posting prohibited | 0.95 | Both transition from initial state on receiving 201 to a posting-prohibited state. |
| Established → Closed | receive 502 greeting / send 502 response; close connection immediately | Receive 502 / Close connection | 0.95 | Both transition from initial state on 502 to closed. |
| Established → Closed | receive 400 greeting / send 400 response; close connection | Receive 400 / Close connection | 0.95 | Both transition from initial state on 400 to closed. |
| ArticleInput → PostingAllowed | receive article fail / send 441 response | Receive 240 / Article posted successfully | 0.7 | Both represent successful article posting (240). Source states match (Posting_Article~ArticleInput). |
| PostingProhibited → PostingProhibited | receive POST command / send 440 response | Receive 441 / Posting failed | 0.7 | Both represent failed article posting (441). Source states match. Destination differs but semantical |

## FTP — 8 state(s) renamed, 13 transition(s) relabelled

| our state | ground-truth name | conf. | judge reason |
|---|---|---|---|
| AwaitingInput | Disconnected | 0.7 | Both are the state where USER command is received to begin login |
| NeedPassword | Connected | 0.85 | Both await PASS after USER/331 |
| LoggedIn | Authenticated | 0.95 | Both represent fully logged-in state |
| NeedAccount | Need_Account | 0.95 | Both represent needing ACCT |
| DataTransfer | Data_Transfer | 0.95 | Both represent active data transfer |
| RestartPending | Restart_Point_Set | 0.95 | Both represent REST marker set, awaiting transfer command |
| RenamePending | Awaiting_RNTO | 0.95 | Both represent RNFR sent, awaiting RNTO |
| Closed | Logged_Out | 0.9 | Both represent session termination |

| edge (after state renames) | our label (event / action) | ground-truth label (event / action) | conf. | judge reason |
|---|---|---|---|---|
| AwaitingInput → NeedPassword | receive USER 331 / send 331 need password | receive USER / reply 331 | 0.9 | USER→331→NeedPassword matches USER→331→Connected |
| NeedPassword → LoggedIn | receive PASS 230 / send 230 user logged in | receive PASS / reply 230 | 0.95 | PASS→230→LoggedIn matches |
| NeedPassword → AwaitingInput | receive PASS 530 / send 530 login failed | receive PASS / reply 530 | 0.9 | PASS→530→back to initial login state |
| NeedPassword → NeedAccount | receive PASS 332 / send 332 need account | receive PASS / reply 332 | 0.95 | PASS→332→NeedAccount matches |
| NeedAccount → LoggedIn | receive ACCT 230 / send 230 user logged in | receive ACCT / reply 230 | 0.85 | ACCT→230→LoggedIn; Accounted merges into LoggedIn |
| LoggedIn → EnteringPassiveMode | receive PASV 227 / send 227 entering passive | receive PASV / reply 227 | 0.9 | PASV→227 matches |
| DataTransfer → LoggedIn | receive reply 2yz / close data connection | Transfer Complete / reply 226 | 0.75 | Transfer complete→2xx→LoggedIn matches semantically |
| DataTransfer → Closed | receive QUIT / close connection after result | receive QUIT / reply 221 | 0.9 | QUIT during transfer→Closed matches |
| LoggedIn → RestartPending | receive REST 350 / send 350 restart marker | receive REST / reply 350 | 0.95 | REST→350→RestartPending matches |
| RestartPending → DataTransfer | send transfer command / send transfer command | receive RETR / reply 150 | 0.6 | Generic transfer command from RestartPending→DataTransfer; RETR is one such command |
| LoggedIn → RenamePending | receive RNFR 350 / send 350 pending info | receive RNFR / reply 350 | 0.95 | RNFR→350→RenamePending matches |
| RenamePending → LoggedIn | receive RNTO 250 / rename file send 250 | receive RNTO / reply 250 | 0.95 | RNTO→250→LoggedIn matches |
| LoggedIn → Closed | receive QUIT / close control connection | receive QUIT / reply 221 | 0.9 | QUIT→Closed matches |
