# Semantic alignment report — our FSM vs PSMBench ground truth, element by element

Supplementary analysis. For every ground-truth state and transition: does the benchmark's string matcher credit it, and does the meaning-level judge (Opus 4.6, one-to-one, confidence >= 0.6) find it in our official export? Nothing here replaces any result. 'string' = PSMBench evaluator at threshold 0.5; 'meaning' = judge.
**Totals over 14 protocols:** states by string 99/108, by meaning 98/108; transitions by string 147/297, by meaning 211/297.

## TCP — states: string 10/11, meaning 11/11 | transitions: string 15/20, meaning 20/20 (both 15) | ours: 11 states, 75 transitions

| GT state | string match (cos) | meaning match (conf) | judge reason |
|---|---|---|---|
| CLOSED | CLOSED (1.0) | CLOSED (1.0) | Same name and same role as initial/final state. |
| LISTEN | LISTEN (1.0) | LISTEN (1.0) | Same name, passive open state. |
| SYN_SENT | SYN_SENT (1.0) | SYN_SENT (1.0) | Same name, active open SYN sent state. |
| SYN_RCVD | SYN_SENT (0.58) | SYN_RECEIVED (1.0) | SYN_RCVD and SYN_RECEIVED are the same state, just different abbreviation. |
| ESTAB | — | ESTABLISHED (1.0) | ESTAB is abbreviation for ESTABLISHED. |
| FIN_WAIT-1 | FIN_WAIT_1 (1.0) | FIN_WAIT_1 (1.0) | Same state, different punctuation. |
| FIN_WAIT-2 | FIN_WAIT_2 (1.0) | FIN_WAIT_2 (1.0) | Same state, different punctuation. |
| CLOSING | CLOSING (1.0) | CLOSING (1.0) | Same name and role. |
| TIME-WAIT | TIME_WAIT (1.0) | TIME_WAIT (1.0) | Same state, different punctuation. |
| CLOSE_WAIT | CLOSE_WAIT (1.0) | CLOSE_WAIT (1.0) | Same name and role. |
| LAST-ACK | LAST_ACK (1.0) | LAST_ACK (1.0) | Same state, different punctuation. |

| GT transition | string | meaning | our matching edge (by meaning) | judge reason |
|---|---|---|---|---|
| CLOSED --[passive OPEN / create TCB]--> LISTEN | yes | yes (1.0) | CLOSED --[passive OPEN / create TCB]--> LISTEN | Exact match. |
| CLOSED --[active OPEN / create TCB; send SYN]--> SYN_SENT | yes | yes (1.0) | CLOSED --[active OPEN / create TCB; send SYN]--> SYN_SENT | Exact match. |
| LISTEN --[receive SYN / send SYN, ACK]--> SYN_RCVD | yes | yes (1.0) | LISTEN --[receive SYN / send SYN_ACK]--> SYN_RECEIVED | Same transition, SYN_ACK = SYN, ACK. |
| LISTEN --[send SYN / send SYN]--> SYN_SENT | yes | yes (0.8) | LISTEN --[SEND / send SYN]--> SYN_SENT | SEND user call triggering SYN send from LISTEN corresponds to the 'send SYN' event in GT. |
| LISTEN --[CLOSE / delete TCB]--> CLOSED | yes | yes (1.0) | LISTEN --[CLOSE / delete TCB]--> CLOSED | Exact match. |
| SYN_SENT --[receive SYN / send SYN, ACK]--> SYN_RCVD | yes | yes (1.0) | SYN_SENT --[receive SYN / send SYN_ACK]--> SYN_RECEIVED | Same transition, simultaneous open case. |
| SYN_SENT --[receive SYN, ACK / send ACK]--> ESTAB | no | yes (1.0) | SYN_SENT --[receive SYN ACK / send ACK]--> ESTABLISHED | Same transition. |
| SYN_SENT --[CLOSE / delete TCB]--> CLOSED | yes | yes (1.0) | SYN_SENT --[CLOSE / delete TCB]--> CLOSED | Exact match. |
| SYN_RCVD --[receive ACK of SYN / ]--> ESTAB | no | yes (0.9) | SYN_RECEIVED --[receive ACK / set SND.WND from segment]--> ESTABLISHED | Receive ACK in SYN_RECEIVED transitions to ESTABLISHED; the ACK is the ACK of SYN. Extra action deta |
| SYN_RCVD --[CLOSE / send FIN]--> FIN_WAIT-1 | yes | yes (1.0) | SYN_RECEIVED --[CLOSE / send FIN]--> FIN_WAIT_1 | Exact match. |
| SYN_RCVD --[rcv RST (note1) / ]--> LISTEN | no | yes (0.95) | SYN_RECEIVED --[receive RST / flush retransmission queue]--> LISTEN | RST received in SYN_RECEIVED returning to LISTEN matches the note1 case (passive open originated). |
| ESTAB --[CLOSE / send FIN]--> FIN_WAIT-1 | no | yes (1.0) | ESTABLISHED --[CLOSE / send FIN]--> FIN_WAIT_1 | Exact match. |
| ESTAB --[receive FIN / send ACK]--> CLOSE_WAIT | no | yes (1.0) | ESTABLISHED --[receive FIN / send ACK; signal connection closing]--> CLOSE_WAIT | Same transition, extra signaling action is fine. |
| FIN_WAIT-1 --[receive FIN / send ACK]--> CLOSING | yes | yes (1.0) | FIN_WAIT_1 --[receive FIN / send ACK]--> CLOSING | Exact match. |
| FIN_WAIT-1 --[receive ACK of FIN / ]--> FIN_WAIT-2 | yes | yes (1.0) | FIN_WAIT_1 --[receive ACK of FIN / ]--> FIN_WAIT_2 | Exact match. |
| FIN_WAIT-2 --[receive FIN / send ACK]--> TIME-WAIT | yes | yes (1.0) | FIN_WAIT_2 --[receive FIN / send ACK; start time_wait timer]--> TIME_WAIT | Same transition, extra timer start detail. |
| CLOSING --[receive ACK of FIN / ]--> TIME-WAIT | yes | yes (1.0) | CLOSING --[receive ACK of FIN / ]--> TIME_WAIT | Exact match. |
| TIME-WAIT --[Timeout=2MSL / delete TCB]--> CLOSED | yes | yes (1.0) | TIME_WAIT --[timeout 2MSL / delete TCB]--> CLOSED | Same transition. |
| CLOSE_WAIT --[CLOSE / send FIN]--> LAST-ACK | yes | yes (1.0) | CLOSE_WAIT --[CLOSE / send FIN]--> LAST_ACK | Exact match. |
| LAST-ACK --[receive ACK of FIN / delete TCB]--> CLOSED | yes | yes (1.0) | LAST_ACK --[receive ACK of FIN / delete TCB]--> CLOSED | Exact match. |

Our unmatched transitions, judge classification: real behaviour not in GT 52, duplicate/alias 1, wrong/unsupported 2.

## DCCP — states: string 9/9, meaning 9/9 | transitions: string 16/25, meaning 15/25 (both 12) | ours: 9 states, 40 transitions

| GT state | string match (cos) | meaning match (conf) | judge reason |
|---|---|---|---|
| CLOSED | CLOSED (1.0) | CLOSED (1.0) | Same name, same initial/final state, same protocol situation. |
| LISTEN | LISTEN (1.0) | LISTEN (1.0) | Same name, same passive-open waiting state. |
| REQUEST | REQUEST (1.0) | REQUEST (1.0) | Same name, client has sent Request and awaits Response. |
| RESPOND | RESPOND (1.0) | RESPOND (1.0) | Same name, server has sent Response and awaits Ack. |
| PARTOPEN | PARTOPEN (1.0) | PARTOPEN (1.0) | Same name, client sent Ack, awaiting first packet from server. |
| OPEN | OPEN (1.0) | OPEN (1.0) | Same name, connection fully established. |
| CLOSEREQ | CLOSEREQ (1.0) | CLOSEREQ (1.0) | Same name, server sent CloseReq. |
| CLOSING | CLOSING (1.0) | CLOSING (1.0) | Same name, endpoint sent Close, awaiting Reset. |
| TIMEWAIT | TIMEWAIT (1.0) | TIMEWAIT (1.0) | Same name, waiting before final cleanup. |

| GT transition | string | meaning | our matching edge (by meaning) | judge reason |
|---|---|---|---|---|
| CLOSED --[active open / Send REQUEST]--> REQUEST | yes | yes (1.0) | CLOSED --[active open / send DCCP-Request]--> REQUEST | Exact match: active open triggers sending Request, transition to REQUEST. |
| CLOSED --[passive open / ]--> LISTEN | yes | yes (1.0) | CLOSED --[passive open / ]--> LISTEN | Exact match: passive open to LISTEN. |
| LISTEN --[receive REQUEST / send RESPONSE]--> RESPOND | yes | yes (1.0) | LISTEN --[receive DCCP-Request / send DCCP-Response]--> RESPOND | Same event and action with DCCP- prefix. |
| LISTEN --[timeout / timeout]--> CLOSED | no | no | — | No candidate transition from LISTEN on timeout to CLOSED found. |
| RESPOND --[receive ACK or DATAACK / ]--> OPEN | yes | yes (0.95) | RESPOND --[receive Ack / ]--> OPEN | Receive Ack matches receive ACK or DATAACK (Ack subsumes the primary case). |
| RESPOND --[timeout / timeout]--> CLOSED | yes | yes (0.85) | RESPOND --[timeout 4MSL / ]--> CLOSED | Both are timeout transitions from RESPOND to CLOSED; candidate specifies 4MSL. |
| RESPOND --[ / send DATA]--> RESPOND | no | no | — | No candidate transition for sending data while staying in RESPOND. |
| REQUEST --[receive RESPONSE / send ACK]--> PARTOPEN | yes | yes (1.0) | REQUEST --[receive DCCP-Response / send DCCP-Ack]--> PARTOPEN | Exact semantic match. |
| REQUEST --[receive RESET? / reset DCCP]--> CLOSED | yes | no | — | Both go from REQUEST to CLOSED, but the events differ: GT says receive RESET, candidate says client  |
| REQUEST --[timeout / timeout]--> CLOSED | no | no | — | Candidate has timeout retransmission staying in REQUEST, not going to CLOSED. No direct match. |
| PARTOPEN --[receive packet / send ACK!]--> OPEN | yes | yes (0.85) | PARTOPEN --[receive valid packet / ]--> OPEN | Both transition from PARTOPEN to OPEN on receiving a valid packet. Candidate omits the send ACK acti |
| PARTOPEN --[receive CLOSEREQ? / send CLOSE!]--> CLOSING | yes | yes (1.0) | PARTOPEN --[receive DCCP-CloseReq / send DCCP-Close]--> CLOSING | Exact semantic match. |
| PARTOPEN --[active close / ]--> CLOSING | yes | no | — | No candidate transition for active close from PARTOPEN. |
| PARTOPEN --[ / send DATAACK]--> PARTOPEN | no | no | — | Both are self-loops in PARTOPEN sending Ack/DataAck. GT has no explicit event; candidate uses timeou |
| PARTOPEN --[timeout / timeout]--> CLOSED | no | yes (0.8) | PARTOPEN --[timeout 4MSL / send DCCP-Reset]--> CLOSED | Both are timeout from PARTOPEN to CLOSED. Candidate specifies 4MSL and sending Reset. |
| PARTOPEN --[receive RESET / send RESET]--> CLOSED | yes | no | — | Candidate has receive DCCP-Reset going to TIMEWAIT (index 21), not CLOSED. Different destination. |
| OPEN --[receive data_ack / ]--> OPEN | yes | no | — | No direct candidate transition for normal data/ack self-loop in OPEN. Candidate has Sync and other s |
| OPEN --[active close / send CLOSEREQ]--> CLOSEREQ | yes | yes (1.0) | OPEN --[server active close / send DCCP-CloseReq]--> CLOSEREQ | Server active close sending CloseReq, exact match. |
| OPEN --[active close / send close]--> CLOSING | yes | yes (1.0) | OPEN --[active close / send DCCP-Close]--> CLOSING | Client active close sending Close, exact match. |
| OPEN --[receive closeReq / send close]--> CLOSING | no | yes (1.0) | OPEN --[receive DCCP-CloseReq / send DCCP-Close]--> CLOSING | Exact semantic match. |
| OPEN --[receive CLOSE / send RESET]--> CLOSED | no | yes (1.0) | OPEN --[receive DCCP-Close / send DCCP-Reset]--> CLOSED | Exact semantic match. |
| CLOSEREQ --[receive CLOSE / send RESET?]--> CLOSED | yes | yes (1.0) | CLOSEREQ --[receive DCCP-Close / send DCCP-Reset]--> CLOSED | Exact semantic match. |
| CLOSING --[receive Reset / ]--> TIMEWAIT | yes | yes (1.0) | CLOSING --[receive DCCP-Reset / ]--> TIMEWAIT | Exact semantic match. |
| CLOSING --[receive Close / Send RESET?]--> CLOSED | no | no | — | Both go from CLOSING to CLOSED, but events differ significantly. GT says receive Close/send Reset; c |
| TIMEWAIT --[receive Close / send RESET]--> CLOSED | no | no | — | Both go TIMEWAIT->CLOSED but events are completely different. No match. |

Our unmatched transitions, judge classification: real behaviour not in GT 23, duplicate/alias 0, wrong/unsupported 0.

## BGP — states: string 6/6, meaning 6/6 | transitions: string 24/26, meaning 20/26 (both 20) | ours: 6 states, 109 transitions

| GT state | string match (cos) | meaning match (conf) | judge reason |
|---|---|---|---|
| Idle | Idle (1.0) | Idle (1.0) | Same name, same initial state, same protocol situation |
| Connect | Connect (1.0) | Connect (1.0) | Same name, same protocol situation - TCP connection being initiated |
| Active | Active (1.0) | Active (1.0) | Same name, same protocol situation - listening for incoming connections |
| OpenSent | OpenSent (1.0) | OpenSent (1.0) | Same name, same protocol situation - OPEN message sent |
| OpenConfirm | OpenConfirm (1.0) | OpenConfirm (1.0) | Same name, same protocol situation - awaiting KEEPALIVE confirmation |
| Established | Established (1.0) | Established (1.0) | Same name, same protocol situation - BGP session established |

| GT transition | string | meaning | our matching edge (by meaning) | judge reason |
|---|---|---|---|---|
| Idle --[ManualStart / Initiate TCP, start ConnectRetryTimer]--> Connect | yes | yes (1.0) | Idle --[ManualStart / ]--> Connect | Same states, same event, same core action of initiating TCP and starting timer |
| Idle --[ManualStart_with_PassiveTcpEstablishment / Start listening, reset ConnectRetryCounter]--> Active | yes | yes (1.0) | Idle --[ManualStart PassiveTcp / ]--> Active | Same states, same event (passive TCP manual start), same action of listening |
| Idle --[AutomaticStart_with_PassiveTcpEstablishment / Start listening, reset ConnectRetryCounter]--> Active | yes | yes (1.0) | Idle --[AutomaticStart PassiveTcp / ]--> Active | Same states, same event (passive TCP automatic start), same action of listening |
| Idle --[AutomaticStop / Do nothing]--> Idle | no | no | — | No candidate transition for AutomaticStop in Idle staying in Idle |
| Idle --[IdleHoldTimer_Expires / Initiate connection after oscillation damping]--> Connect | no | no | — | No candidate transition for IdleHoldTimer_Expires from Idle to Connect |
| Connect --[TcpConnectionFails / Reset ConnectRetryTimer, listen for incoming connection]--> Active | yes | yes (1.0) | Connect --[TcpConnectionFails / ]--> Active | Same states, same event, action includes restart ConnectRetryTimer and listen |
| Connect --[TcpConnectionConfirmed / Send OPEN, start HoldTimer]--> OpenSent | yes | yes (0.95) | Connect --[TcpConnectionConfirmed / ]--> OpenSent | Same states, same event, action sends OPEN and sets HoldTimer |
| Connect --[DelayOpenTimer_Expires / Send OPEN, start HoldTimer]--> OpenSent | yes | yes (1.0) | Connect --[DelayOpenTimer Expires / ]--> OpenSent | Same states, same event, action sends OPEN and sets HoldTimer |
| Connect --[ConnectRetryTimer_Expires / Reset ConnectRetryCounter, release resources]--> Idle | yes | no | — | GT goes to Idle; candidate index 5 goes Connect->Connect. No matching candidate transition. |
| Active --[TcpConnection_Valid / Initiate TCP connection]--> Connect | yes | no | — | No direct match. GT has Active->Connect on TcpConnection_Valid. Candidate index 32 has Active->Activ |
| Active --[TcpConnectionFails / Reset ConnectRetryCounter, release resources]--> Idle | yes | yes (1.0) | Active --[TcpConnectionFails / ]--> Idle | Same states, same event, action releases resources |
| Active --[DelayOpenTimer_Expires / Send OPEN, start HoldTimer]--> OpenSent | yes | yes (1.0) | Active --[DelayOpenTimer Expires / ]--> OpenSent | Same states, same event, action sends OPEN |
| Active --[ConnectRetryTimer_Expires / Reset ConnectRetryCounter, release resources]--> Idle | yes | no | — | GT has Active->Idle on ConnectRetryTimer_Expires. Candidate 30 goes Active->Connect on ConnectRetryT |
| OpenSent --[BGPOpen / Send KEEPALIVE, start HoldTimer]--> OpenConfirm | yes | yes (1.0) | OpenSent --[BGPOpen / ]--> OpenConfirm | Same states, same event, action sends KEEPALIVE and sets HoldTimer |
| OpenSent --[TcpConnectionFails / Reset ConnectRetryCounter, release resources]--> Idle | yes | no | — | Same source and event but GT goes to Idle while candidate goes to Active. Different destination. |
| OpenSent --[BGPOpenMsgErr / Send NOTIFICATION, reset ConnectRetryCounter, release resources]--> Idle | yes | yes (1.0) | OpenSent --[BGPOpenMsgErr / ]--> Idle | Same states, same event, action sends NOTIFICATION |
| OpenSent --[OpenCollisionDump / Send NOTIFICATION, reset ConnectRetryCounter, release resources]--> Idle | yes | yes (1.0) | OpenSent --[OpenCollisionDump / ]--> Idle | Same states, same event, action sends NOTIFICATION with Cease |
| OpenConfirm --[KeepAliveMsg / Restart HoldTimer]--> Established | yes | yes (1.0) | OpenConfirm --[KeepAliveMsg / ]--> Established | Same states, same event, action restarts HoldTimer |
| OpenConfirm --[HoldTimer_Expires / Send NOTIFICATION, reset ConnectRetryCounter, release resources]--> Idle | yes | yes (1.0) | OpenConfirm --[HoldTimer Expires / ]--> Idle | Same states, same event, action sends NOTIFICATION |
| OpenConfirm --[NotifMsgVerErr / Send NOTIFICATION, reset ConnectRetryCounter, release resources]--> Idle | yes | yes (1.0) | OpenConfirm --[NotifMsgVerErr / ]--> Idle | Same states, same event, action releases resources |
| Established --[ManualStop / Send NOTIFICATION, reset ConnectRetryCounter, release resources]--> Idle | yes | yes (1.0) | Established --[ManualStop / ]--> Idle | Same states, same event, action sends NOTIFICATION with Cease |
| Established --[AutomaticStop / Send NOTIFICATION, reset ConnectRetryCounter, release resources]--> Idle | yes | yes (1.0) | Established --[AutomaticStop / ]--> Idle | Same states, same event, action sends NOTIFICATION with Cease |
| Established --[HoldTimer_Expires / Send NOTIFICATION, reset ConnectRetryCounter, release resources]--> Idle | yes | yes (1.0) | Established --[HoldTimer Expires / ]--> Idle | Same states, same event, action sends NOTIFICATION |
| Established --[NotifMsgVerErr / Send NOTIFICATION, reset ConnectRetryCounter, release resources]--> Idle | yes | yes (1.0) | Established --[NotifMsgVerErr / ]--> Idle | Same states, same event, action releases resources |
| Established --[TcpConnectionFails / Reset ConnectRetryCounter, release resources]--> Idle | yes | yes (1.0) | Established --[TcpConnectionFails / ]--> Idle | Same states, same event, action releases resources |
| Established --[UpdateMsgErr / Send NOTIFICATION, reset ConnectRetryCounter, release resources]--> Idle | yes | yes (1.0) | Established --[UpdateMsgErr / ]--> Idle | Same states, same event, action sends NOTIFICATION |

Our unmatched transitions, judge classification: real behaviour not in GT 87, duplicate/alias 2, wrong/unsupported 0.

## PPP — states: string 10/10, meaning 10/10 | transitions: string 20/27, meaning 26/27 (both 20) | ours: 15 states, 138 transitions

| GT state | string match (cos) | meaning match (conf) | judge reason |
|---|---|---|---|
| Initial | Initial (1.0) | Initial (1.0) | Same state name and role |
| Starting | Starting (1.0) | Starting (1.0) | Same state name and role |
| Closed | Closed (1.0) | Closed (1.0) | Same state name and role |
| Stopped | Stopped (1.0) | Stopped (1.0) | Same state name and role |
| Closing | Closing (1.0) | Closing (1.0) | Same state name and role |
| Stopping | Stopping (1.0) | Stopping (1.0) | Same state name and role |
| ReqSent | ReqSent (1.0) | ReqSent (1.0) | Same state name and role |
| AckRcvd | AckRcvd (1.0) | AckRcvd (1.0) | Same state name and role |
| AckSent | AckSent (1.0) | AckSent (1.0) | Same state name and role |
| Opened | Opened (1.0) | Opened (1.0) | Same state name and role |

| GT transition | string | meaning | our matching edge (by meaning) | judge reason |
|---|---|---|---|---|
| Initial --[receive lower layer up (Up) / send This-Layer-Up (tlu)]--> Closed | no | yes (0.8) | Initial --[Up / ]--> Closed | Same states and event Up. GT says tlu action but candidate omits it; the RFC table for Initial/Up ac |
| Initial --[receive administrative Open (Open) / send This-Layer-Started (tls)]--> Starting | no | yes (1.0) | Initial --[Open / signal this layer started]--> Starting | Same states, event Open, action tls |
| Starting --[receive lower layer up (Up) / send Initialize-Restart-Count (irc), send Configure-Request (scr)]--> ReqSent | yes | yes (1.0) | Starting --[Up / initialize restart count; send Configure-Request]--> ReqSent | Exact match |
| Closed --[receive lower layer down (Down) / send This-Layer-Finished (tlf)]--> Initial | no | yes (0.8) | Closed --[Down / ]--> Initial | Same states and event. Candidate omits tlf but RFC table for Closed/Down has no tlf, so same transit |
| Closed --[receive administrative Open (Open) / send Initialize-Restart-Count (irc), send Configure-Request (scr)]--> ReqSent | yes | yes (1.0) | Closed --[Open / initialize restart count; send Configure-Request]--> ReqSent | Exact match |
| Closed --[receive administrative Close (Close) / ]--> Closed | no | yes (1.0) | Closed --[Close / ]--> Closed | Exact match |
| Stopped --[receive lower layer up (Up) / send This-Layer-Started (tls)]--> Starting | no | yes (0.7) | Stopped --[Down / signal this layer started]--> Starting | GT says Up but candidate says Down. RFC table actually has Down->Starting/tls for Stopped. The GT ev |
| Stopped --[receive administrative Close (Close) / send This-Layer-Finished (tlf)]--> Closed | yes | yes (0.8) | Stopped --[Close / ]--> Closed | Same states and event Close. Candidate omits tlf but same transition. |
| ReqSent --[receive administrative Close (Close) / send Terminate-Request (str), send Initialize-Restart-Count (irc)]--> Closing | yes | yes (1.0) | ReqSent --[Close / initialize restart count; send Terminate-Request]--> Closing | Exact match |
| ReqSent --[receive Terminate-Request (RTR) / send Terminate-Request (str)]--> Stopping | no | no | — | Same source and event RTR but different destination (Stopping vs ReqSent) and different action. The  |
| ReqSent --[timeout counter > 0 (TO+) / send Configure-Request (scr)]--> ReqSent | yes | yes (1.0) | ReqSent --[timeout TO plus / send Configure-Request]--> ReqSent | Exact match |
| ReqSent --[timeout counter expired (TO-) / send This-Layer-Finished (tlf)]--> Stopped | yes | yes (1.0) | ReqSent --[timeout TO minus / signal this layer finished]--> Stopped | Exact match |
| ReqSent --[receive Configure-Ack (RCA) / send Initialize-Restart-Count (irc)]--> AckRcvd | yes | yes (1.0) | ReqSent --[receive RCA / initialize restart count]--> AckRcvd | Exact match |
| ReqSent --[receive Configure-Request (RCR+) / send Configure-Ack (sca)]--> AckSent | no | yes (1.0) | ReqSent --[receive RCR plus / send Configure-Ack]--> AckSent | Exact match |
| AckRcvd --[receive administrative Close (Close) / send Terminate-Request (str), send Initialize-Restart-Count (irc)]--> Closing | yes | yes (1.0) | AckRcvd --[Close / initialize restart count; send Terminate-Request]--> Closing | Exact match |
| AckRcvd --[receive Configure-Request (RCR+) / send Configure-Ack (sca), send This-Layer-Up (tlu)]--> Opened | yes | yes (1.0) | AckRcvd --[receive RCR plus / send Configure-Ack; signal this layer up]--> Opened | Exact match |
| AckRcvd --[receive Configure-Nak/Rej (RCN) / send Configure-Request (scr)]--> ReqSent | yes | yes (1.0) | AckRcvd --[receive RCN / send Configure-Request]--> ReqSent | Exact match |
| AckSent --[receive administrative Close (Close) / send Terminate-Request (str), send Initialize-Restart-Count (irc)]--> Closing | yes | yes (1.0) | AckSent --[Close / initialize restart count; send Terminate-Request]--> Closing | Exact match |
| AckSent --[receive Configure-Ack (RCA) / send This-Layer-Up (tlu)]--> Opened | yes | yes (1.0) | AckSent --[receive RCA / initialize restart count; signal this layer up]--> Opened | Same transition; candidate adds irc which is per RFC |
| AckSent --[receive Configure-Request (RCR-) / send Configure-Request (scr)]--> ReqSent | yes | yes (0.8) | AckSent --[receive RCR minus / send Configure-Nak]--> ReqSent | Same states and event RCR-. Action differs (scr vs scn) but both are valid parts of the response to  |
| Opened --[receive administrative Close (Close) / send Terminate-Request (str), send Initialize-Restart-Count (irc)]--> Closing | yes | yes (1.0) | Opened --[Close / signal this layer down; initialize restart count; send Terminate-Request]--> Closing | Same transition; candidate adds tld which is per RFC |
| Opened --[receive Terminate-Request (RTR) / send Terminate-Request (str)]--> Stopping | yes | yes (0.85) | Opened --[receive RTR / signal this layer down; zero restart count; send Terminate-Ack]--> Stopping | Same states and event RTR. GT says send str but RFC says send Terminate-Ack; candidate is more corre |
| Stopping --[receive Terminate-Ack (RTA) / send This-Layer-Finished (tlf)]--> Stopped | yes | yes (1.0) | Stopping --[receive RTA / signal this layer finished]--> Stopped | Exact match |
| Stopping --[timeout counter > 0 (TO+) / send Terminate-Request (str)]--> Stopping | yes | yes (1.0) | Stopping --[timeout TO plus / send Terminate-Request]--> Stopping | Exact match |
| Stopping --[timeout counter expired (TO-) / send This-Layer-Finished (tlf)]--> Stopped | yes | yes (1.0) | Stopping --[timeout TO minus / signal this layer finished]--> Stopped | Exact match |
| Closing --[receive Terminate-Ack (RTA) / send This-Layer-Finished (tlf)]--> Closed | yes | yes (1.0) | Closing --[receive RTA / signal this layer finished]--> Closed | Exact match |
| Closing --[timeout counter expired (TO-) / send This-Layer-Finished (tlf)]--> Closed | yes | yes (1.0) | Closing --[timeout TO minus / signal this layer finished]--> Closed | Exact match |

Our unmatched transitions, judge classification: real behaviour not in GT 96, duplicate/alias 0, wrong/unsupported 16.

## DHCP — states: string 8/8, meaning 8/8 | transitions: string 14/19, meaning 15/19 (both 13) | ours: 10 states, 30 transitions

| GT state | string match (cos) | meaning match (conf) | judge reason |
|---|---|---|---|
| INIT | INIT (1.0) | INIT (1.0) | Same name, same initial state, same protocol situation. |
| INIT-REBOOT | INIT_REBOOT (1.0) | INIT_REBOOT (1.0) | Same state, just hyphen vs underscore naming difference. |
| REBOOTING | REBOOTING (1.0) | REBOOTING (1.0) | Identical state name and meaning. |
| SELECTING | SELECTING (1.0) | SELECTING (1.0) | Identical state name and meaning. |
| REQUESTING | REQUESTING (1.0) | REQUESTING (1.0) | Identical state name and meaning. |
| BOUND | BOUND (1.0) | BOUND (1.0) | Identical state name and meaning. |
| RENEWING | RENEWING (1.0) | RENEWING (1.0) | Identical state name and meaning. |
| REBINDING | REBINDING (1.0) | REBINDING (1.0) | Identical state name and meaning. |

| GT transition | string | meaning | our matching edge (by meaning) | judge reason |
|---|---|---|---|---|
| INIT --[ / Send DHCPDISCOVER]--> SELECTING | yes | yes (1.0) | INIT --[send DHCPDISCOVER / broadcast DHCPDISCOVER]--> SELECTING | Both transition from INIT to SELECTING by sending DHCPDISCOVER. |
| SELECTING --[Select offer / Send DHCPREQUEST]--> REQUESTING | yes | yes (1.0) | SELECTING --[select offer / select server from offers; send DHCPREQUEST]--> REQUESTING | Both select an offer and send DHCPREQUEST, transitioning SELECTING->REQUESTING. |
| SELECTING --[receive DHCPOFFER / Collect replies]--> SELECTING | yes | yes (1.0) | SELECTING --[receive DHCPOFFER / collect replies]--> SELECTING | Identical: receive DHCPOFFER, collect replies, stay in SELECTING. |
| REQUESTING --[receive DHCPACK / Record lease Set timers]--> BOUND | yes | yes (1.0) | REQUESTING --[receive DHCPACK / record lease; set timer T1; set timer T2]--> BOUND | Both receive DHCPACK in REQUESTING, record lease, set timers, go to BOUND. |
| REQUESTING --[receive DHCPACK (not accept) / Send DHCPDECLINE]--> INIT | yes | no | — | Candidate index 12 (SELECTING receive DHCPACK unacceptable -> DHCPDECLINE -> INIT) is from SELECTING |
| REQUESTING --[receive DHCPNAK / Discard offer]--> INIT | no | yes (1.0) | REQUESTING --[receive DHCPNAK / discard offer; restart configuration]--> INIT | Both receive DHCPNAK in REQUESTING, discard offer, go to INIT. |
| REQUESTING --[receive DHCPOFFER / Discard]--> REQUESTING | yes | yes (1.0) | REQUESTING --[receive DHCPOFFER / discard]--> REQUESTING | Identical: discard DHCPOFFER while in REQUESTING. |
| INIT-REBOOT --[ / Send DHCPREQUEST]--> REBOOTING | yes | yes (1.0) | INIT_REBOOT --[send DHCPREQUEST / send DHCPREQUEST]--> REBOOTING | Both transition from INIT-REBOOT to REBOOTING by sending DHCPREQUEST. |
| REBOOTING --[receive DHCPACK / Record lease Set timers T1 T2]--> BOUND | yes | yes (1.0) | REBOOTING --[receive DHCPACK / record lease; set timer T1; set timer T2]--> BOUND | Both receive DHCPACK in REBOOTING, record lease, set timers, go to BOUND. |
| REBOOTING --[receive DHCPNAK / Restart]--> INIT | yes | yes (1.0) | REBOOTING --[receive DHCPNAK / restart]--> INIT | Identical: receive DHCPNAK in REBOOTING, restart, go to INIT. |
| BOUND --[timer T1 expires / Send DHCPREQUEST]--> RENEWING | yes | yes (1.0) | BOUND --[timeout T1 expires / send DHCPREQUEST to server]--> RENEWING | Both: T1 expires in BOUND, send DHCPREQUEST, go to RENEWING. |
| BOUND --[receive DHCPOFFER / Discard]--> BOUND | no | yes (0.6) | BOUND --[receive unsolicited msg / discard]--> BOUND | Candidate uses 'unsolicited msg' which encompasses DHCPOFFER, DHCPACK, DHCPNAK. This is a generalize |
| BOUND --[receive DHCPACK / Discard]--> BOUND | no | no | — | Candidate index 18 (unsolicited msg) could cover this but already matched to GT index 11. |
| BOUND --[receive DHCPNAK / Discard]--> BOUND | no | no | — | Candidate index 18 (unsolicited msg) could cover this but already matched to GT index 11. |
| RENEWING --[Receive DHCPACK / Record lease; Set timers T1, T2]--> BOUND | yes | yes (1.0) | RENEWING --[receive DHCPACK / record lease; set timer T1; set timer T2]--> BOUND | Identical: receive DHCPACK in RENEWING, record lease, set timers, go to BOUND. |
| RENEWING --[timer T2 expires / send Broadcast DHCPREQUEST]--> REBINDING | yes | yes (1.0) | RENEWING --[timeout T2 expires / broadcast DHCPREQUEST]--> REBINDING | Both: T2 expires in RENEWING, broadcast DHCPREQUEST, go to REBINDING. |
| RENEWING --[receive DHCPNAK / Halt network]--> INIT | no | no | — | No candidate transition from RENEWING on DHCPNAK. The candidate omits this transition. |
| REBINDING --[receive DHCPACK / Record lease; Set timers T1, T2]--> BOUND | yes | yes (1.0) | REBINDING --[receive DHCPACK / record lease; set timer T1; set timer T2]--> BOUND | Identical: receive DHCPACK in REBINDING, record lease, set timers, go to BOUND. |
| REBINDING --[receive DHCPNAK / Halt network]--> INIT | yes | yes (1.0) | REBINDING --[receive DHCPNAK / halt network]--> INIT | Identical: receive DHCPNAK in REBINDING, halt network, go to INIT. |

Our unmatched transitions, judge classification: real behaviour not in GT 12, duplicate/alias 0, wrong/unsupported 3.

## PPTP — states: string 9/9, meaning 8/9 | transitions: string 9/19, meaning 11/19 (both 6) | ours: 11 states, 37 transitions

| GT state | string match (cos) | meaning match (conf) | judge reason |
|---|---|---|---|
| Idle | idle (1.0) | idle (1.0) | Both are the initial resting state |
| Wait Control Reply | wait_stop_reply (0.69) | wait_ctl_reply (1.0) | Both wait for Start-Control-Connection-Reply |
| Collision Handling | collision (0.83) | collision (0.6) | Both represent collision handling, though candidate never transitions into collision state |
| Established | established (1.0) | established (1.0) | Both represent active connection |
| Wait Stop Reply | wait_stop_reply (1.0) | wait_stop_reply (1.0) | Both wait for Stop-Control-Connection-Reply |
| Wait Incoming Reply | wait_stop_reply (0.71) | — | No distinct candidate state; wait_reply is shared for multiple call types |
| Wait Outgoing Reply | wait_stop_reply (0.63) | wait_reply (0.7) | Candidate wait_reply used for outgoing call reply waiting among others |
| Wait Connect | wait_connect (1.0) | wait_connect (1.0) | Both wait for Incoming-Call-Connected |
| Wait Disconnect | wait_disconnect (1.0) | wait_disconnect (1.0) | Both wait for Call-Disconnect-Notify after Call-Clear-Request |

| GT transition | string | meaning | our matching edge (by meaning) | judge reason |
|---|---|---|---|---|
| Established --[Local Terminate / Send Call Clear Request]--> Wait Disconnect | yes | yes (1.0) | established --[cond local terminate / send CallClearRequest]--> wait_disconnect | Same event, action, and state transition for call-level local terminate |
| Wait Disconnect --[Timeout (60s) / Close TCP and release resources]--> Idle | yes | no | — | No timeout transition from wait_disconnect in candidate |
| Idle --[TCP Open Indication / Send Start Control Connection Request]--> Wait Control Reply | yes | yes (1.0) | idle --[cond TCP established / send StartCtlConnRequest]--> wait_ctl_reply | TCP open triggers sending Start-Control-Connection-Request |
| Wait Control Reply --[Collision Detected / Handle collision (higher IP wins)]--> Collision Handling | no | no | — | Candidate collision transition goes to idle, not to collision state |
| Collision Handling --[TCP Termination from loser / Accept winner request]--> Wait Control Reply | no | no | — | No transition from collision state in candidate |
| Collision Handling --[Receive Start Control Connection Reply (Version OK) / Accept winner connection]--> Established | no | no | — | No transition from collision state in candidate |
| Wait Control Reply --[Receive Start Control Connection Reply (Version OK) / Establish control connection]--> Established | yes | yes (1.0) | wait_ctl_reply --[receive StartCtlReply ok / establish control connection]--> established | Exact match on receiving successful reply |
| Wait Control Reply --[Receive Start Control Connection Reply (Version Not OK) / Send Stop Control Connection Request]--> Wait Stop Reply | yes | yes (1.0) | wait_ctl_reply --[receive StartCtlReply unsupported / send StopCtlConnRequest]--> wait_stop_reply | Version mismatch triggers stop request |
| Wait Control Reply --[Timeout (60s) / Close TCP]--> Idle | no | no | — | No timeout from wait_ctl_reply in candidate |
| Established --[Local Terminate / Send Stop Control Connection Request]--> Wait Stop Reply | no | yes (1.0) | established --[cond local terminate / send StopCtlConnRequest]--> wait_stop_reply | Control-level local terminate sends stop request |
| Established --[Receive Stop Control Connection Request / Send Stop Control Connection Reply]--> Wait Stop Reply | no | yes (0.6) | established --[receive StopCtlConnRequest / send StopCtlConnReply; close TCP connection]--> idle | Same event and action but candidate goes to idle instead of wait_stop_reply; partial match |
| Wait Stop Reply --[Receive Stop Control Connection Reply / Close TCP, return to Idle]--> Idle | no | yes (1.0) | wait_stop_reply --[receive StopCtlConnReply / close TCP connection]--> idle | Exact match |
| Idle --[Receive Incoming Call Request / Send Incoming Call Reply]--> Wait Incoming Reply | yes | no | — | Similar but candidate goes to wait_connect and specifies accepted; GT has intermediate Wait Incoming |
| Wait Incoming Reply --[Receive Incoming Call Reply (Accept) / Wait for Incoming Call Connected]--> Wait Connect | no | no | — | Candidate skips Wait Incoming Reply state for PNS side |
| Wait Connect --[Receive Incoming Call Connected / Confirm session establishment]--> Established | yes | yes (1.0) | wait_connect --[receive InCallConnected / ]--> established | Same transition: receive connected, move to established |
| Wait Incoming Reply --[Receive Incoming Call Reply (Reject) / Return to Idle]--> Idle | yes | no | — | No matching state for Wait Incoming Reply in candidate |
| Idle --[Send Outgoing Call Request / Wait for PAC response]--> Wait Outgoing Reply | no | yes (0.9) | idle --[cond open indication / send OutgoingCallRequest]--> wait_reply | PNS sends outgoing call request and waits |
| Wait Outgoing Reply --[Receive Outgoing Call Reply (No Error) / Establish call]--> Established | yes | yes (1.0) | wait_reply --[receive OutCallReply ok / connect telco call]--> established | Successful outgoing call reply establishes call |
| Wait Outgoing Reply --[Receive Outgoing Call Reply (Error) / Return to Idle]--> Idle | no | yes (1.0) | wait_reply --[receive OutCallReply error / ]--> idle | Error reply returns to idle |

Our unmatched transitions, judge classification: real behaviour not in GT 24, duplicate/alias 0, wrong/unsupported 1.

## IMAP — states: string 4/5, meaning 5/5 | transitions: string 5/11, meaning 11/11 (both 5) | ours: 9 states, 53 transitions

| GT state | string match (cos) | meaning match (conf) | judge reason |
|---|---|---|---|
| Connection_Established_Server_Greeting | — | ServerGreeting (0.85) | Both represent the state where the server issues its initial greeting (OK, PREAUTH, or BYE). The candidate spl |
| Not_Authenticated | Authenticated (0.86) | NotAuthenticated (1.0) | Same protocol state: client connected but not yet authenticated. |
| Authenticated | Authenticated (1.0) | Authenticated (1.0) | Same protocol state: client authenticated but no mailbox selected. |
| Selected | Selected (1.0) | Selected (1.0) | Same protocol state: a mailbox is selected. |
| Logout | Logout (1.0) | Logout (0.9) | Both represent the logout/disconnecting state. The candidate has a further ConnectionClosed state, but Logout  |

| GT transition | string | meaning | our matching edge (by meaning) | judge reason |
|---|---|---|---|---|
| Connection_Established_Server_Greeting --[connect without pre-authentication / Send OK greeting]--> Not_Authenticated | no | yes (0.9) | ServerGreeting --[receive OK greeting / send OK greeting]--> NotAuthenticated | Both transition from the greeting state to NotAuthenticated upon an OK greeting. |
| Connection_Established_Server_Greeting --[Connects with pre-authentication / Send PREAUTH greeting]--> Authenticated | no | yes (0.95) | ServerGreeting --[receive PREAUTH greeting / send PREAUTH greeting]--> Authenticated | Both transition from greeting state to Authenticated upon PREAUTH. |
| Connection_Established_Server_Greeting --[Connection reject / Send BYE greeting]--> Logout | no | yes (0.95) | ServerGreeting --[receive BYE greeting / send BYE greeting]--> Logout | Both transition from greeting state to Logout upon BYE greeting (connection rejected). |
| Not_Authenticated --[Sends LOGIN or AUTHENTICATE command / Sends LOGIN or AUTHENTICATE command]--> Authenticated | yes | yes (0.8) | NotAuthenticated --[receive AUTHENTICATE OK / enter Authenticated state]--> Authenticated | Both represent successful authentication transitioning to Authenticated. Candidate splits LOGIN and  |
| Not_Authenticated --[Sends LOGOUT or server shutdown / Close connection]--> Logout | yes | yes (0.8) | NotAuthenticated --[send LOGOUT command / send BYE untagged response; send tagged OK response]--> Logout | Both represent logout from NotAuthenticated state. GT combines LOGOUT and server shutdown; this matc |
| Authenticated --[Sends SELECT or EXAMINE command / Sends SELECT or EXAMINE command]--> Selected | no | yes (0.85) | Authenticated --[receive SELECT OK / select mailbox for access]--> Selected | Both represent successful SELECT transitioning from Authenticated to Selected. |
| Authenticated --[Sends LOGOUT or server shutdown / Close connection]--> Logout | yes | yes (0.8) | Authenticated --[send LOGOUT command / send BYE untagged response; send tagged OK response]--> Logout | Both represent logout from Authenticated. GT combines LOGOUT and server shutdown; this matches LOGOU |
| Authenticated --[Send requests mailbox listing / List mailbox]--> Authenticated | no | yes (0.95) | Authenticated --[send LIST command / send untagged LIST responses]--> Authenticated | Both represent listing mailboxes while remaining in Authenticated state. |
| Selected --[Sends CLOSE, UNSELECT, or SELECT/EXAMINE fails / Sends CLOSE, UNSELECT, or SELECT/EXAMINE fails]--> Authenticated | yes | yes (0.8) | Selected --[send CLOSE command / close selected mailbox; send tagged OK response]--> Authenticated | Both represent returning from Selected to Authenticated via CLOSE. GT combines CLOSE/UNSELECT/fail;  |
| Selected --[Client manages messages / Read/write/delete messages]--> Selected | no | yes (0.7) | Selected --[send FETCH command / send untagged FETCH responses]--> Selected | FETCH is one form of message management (reading). GT is a general 'manages messages' self-loop. |
| Selected --[Sends LOGOUT or server shutdown / Close connection]--> Logout | yes | yes (0.8) | Selected --[send LOGOUT command / send BYE untagged response; send tagged OK response]--> Logout | Both represent logout from Selected state. GT combines LOGOUT and server shutdown. |

Our unmatched transitions, judge classification: real behaviour not in GT 27, duplicate/alias 15, wrong/unsupported 0.

## POP3 — states: string 3/3, meaning 3/3 | transitions: string 8/18, meaning 13/18 (both 7) | ours: 4 states, 20 transitions

| GT state | string match (cos) | meaning match (conf) | judge reason |
|---|---|---|---|
| AUTHORIZATION | AUTHORIZATION (1.0) | AUTHORIZATION (1.0) | Same name, same protocol phase for authenticating the user. |
| TRANSACTION | TRANSACTION (1.0) | TRANSACTION (1.0) | Same name, same protocol phase for mailbox operations. |
| UPDATE | UPDATE (1.0) | UPDATE (1.0) | Same name, same protocol phase for finalizing and deleting messages. |

| GT transition | string | meaning | our matching edge (by meaning) | judge reason |
|---|---|---|---|---|
| AUTHORIZATION --[send greeting / reply +OK POP3 server ready]--> AUTHORIZATION | no | yes (0.6) | WaitForConnection --[receive connection / send greeting]--> AUTHORIZATION | Both represent the greeting event that leads to the AUTHORIZATION state. The GT has it as a self-loo |
| AUTHORIZATION --[receive USER / reply request password]--> AUTHORIZATION | no | yes (0.9) | AUTHORIZATION --[receive USER / process USER command]--> AUTHORIZATION | Both handle the USER command in AUTHORIZATION state, staying in AUTHORIZATION. Processing the USER c |
| AUTHORIZATION --[receive PASS / cond check password]--> AUTHORIZATION | no | no | — | No candidate transition explicitly handles the PASS command. |
| AUTHORIZATION --[cond valid USER/PASS / set authenticated true]--> TRANSACTION | no | yes (0.85) | AUTHORIZATION --[cond auth success / acquire maildrop lock; assign message numbers; send positive response]--> TRANSACTION | Both represent successful authentication transitioning from AUTHORIZATION to TRANSACTION. The candid |
| AUTHORIZATION --[receive APOP / cond check digest]--> AUTHORIZATION | yes | no | — | The GT transition represents receiving APOP and checking the digest (staying in AUTHORIZATION for th |
| AUTHORIZATION --[cond valid APOP / set authenticated true]--> TRANSACTION | no | yes (0.9) | AUTHORIZATION --[receive APOP success / send +OK response; enter TRANSACTION state]--> TRANSACTION | Both represent successful APOP authentication transitioning to TRANSACTION. |
| AUTHORIZATION --[cond open maildrop failed / reply -ERR and release lock]--> AUTHORIZATION | yes | yes (0.8) | AUTHORIZATION --[cond auth rejected lock / release maildrop lock; send -ERR response]--> AUTHORIZATION | Both represent a failure to open/lock the maildrop, sending -ERR and releasing the lock, staying in  |
| AUTHORIZATION --[receive QUIT / reply +OK and close TCP]--> UPDATE | no | yes (0.7) | AUTHORIZATION --[receive QUIT / send +OK response; close TCP connection]--> WaitForConnection | Both handle QUIT in AUTHORIZATION with +OK and close TCP. Destination differs (UPDATE vs WaitForConn |
| TRANSACTION --[receive STAT / reply message count size]--> TRANSACTION | yes | yes (0.9) | TRANSACTION --[receive STAT / ]--> TRANSACTION | Same event and state transition. Candidate omits action detail but the meaning is the same. |
| TRANSACTION --[receive LIST / reply scan listing]--> TRANSACTION | yes | yes (0.9) | TRANSACTION --[receive LIST / ]--> TRANSACTION | Same event and state transition for LIST command. |
| TRANSACTION --[receive RETR / reply full message]--> TRANSACTION | yes | yes (0.9) | TRANSACTION --[receive RETR / ]--> TRANSACTION | Same event and state transition for RETR command. |
| TRANSACTION --[receive DELE / set message deleted]--> TRANSACTION | yes | yes (1.0) | TRANSACTION --[receive DELE / mark message as deleted; reply +OK]--> TRANSACTION | Same event, same action (mark as deleted), same state transition. |
| TRANSACTION --[receive NOOP / reply OK]--> TRANSACTION | yes | yes (0.9) | TRANSACTION --[receive NOOP / ]--> TRANSACTION | Same event and state transition for NOOP. |
| TRANSACTION --[receive RSET / reset deleted flags]--> TRANSACTION | yes | yes (1.0) | TRANSACTION --[receive RSET / unmark all deleted messages; reply +OK]--> TRANSACTION | Same event, same action (reset/unmark deleted), same state transition. |
| TRANSACTION --[receive QUIT / set session closing]--> UPDATE | no | yes (1.0) | TRANSACTION --[receive QUIT / enter UPDATE state]--> UPDATE | Both handle QUIT in TRANSACTION transitioning to UPDATE. |
| UPDATE --[cond deleted messages exist / delete marked messages]--> UPDATE | no | no | — | Both involve deleting marked messages in the UPDATE state. The candidate combines deletion and clean |
| UPDATE --[cond cleanup complete / release lock and close TCP]--> UPDATE | no | no | — | The candidate merges cleanup into transition 18; no separate cleanup-complete transition exists. |
| UPDATE --[timeout 5s / close TCP connection]--> UPDATE | no | no | — | No candidate transition handles a timeout scenario. |

Our unmatched transitions, judge classification: real behaviour not in GT 5, duplicate/alias 0, wrong/unsupported 0.

## SIP — states: string 5/5, meaning 5/5 | transitions: string 17/20, meaning 20/20 (both 17) | ours: 22 states, 65 transitions

| GT state | string match (cos) | meaning match (conf) | judge reason |
|---|---|---|---|
| Calling | Calling (1.0) | Calling (1.0) | Same initial state for INVITE client transaction |
| Proceeding | Proceeding (1.0) | Proceeding (1.0) | Same provisional response processing state |
| Completed | Completed (1.0) | Completed (1.0) | Same state after final non-2xx response |
| Confirmed | Confirmed (1.0) | Confirmed (1.0) | Same state after ACK received |
| Terminated | Terminated (1.0) | Terminated (1.0) | Same terminal state |

| GT transition | string | meaning | our matching edge (by meaning) | judge reason |
|---|---|---|---|---|
| Calling --[timeout Timer A / send INVITE]--> Calling | yes | yes (1.0) | Calling --[timeout TimerA / retransmit INVITE request]--> Calling | Same Timer A retransmission of INVITE |
| Calling --[receive 1xx / send 1xx to TU]--> Proceeding | yes | yes (1.0) | Calling --[receive 1xx / pass response to TU]--> Proceeding | Same 1xx handling in Calling |
| Calling --[receive 2xx / send 2xx to TU]--> Terminated | yes | yes (1.0) | Calling --[receive 2xx / pass 2xx to TU]--> Terminated | Same 2xx handling in Calling |
| Calling --[timeout Timer B or Transport Error / inform TU]--> Terminated | yes | yes (0.8) | Calling --[timeout TimerB / inform TU of timeout]--> Terminated | Timer B part matches; transport error is separate in candidate (index 5) |
| Calling --[receive 300-699 / send ACK, send response to TU]--> Completed | yes | yes (1.0) | Calling --[receive 3xx to 6xx / pass response to TU; generate ACK; start TimerD]--> Completed | Same 3xx-6xx handling with ACK generation |
| Proceeding --[receive 2xx / send 2xx to TU]--> Terminated | yes | yes (1.0) | Proceeding --[receive 2xx / pass 2xx to TU]--> Terminated | Same 2xx in Proceeding |
| Proceeding --[receive 300-699 / send ACK, send response to TU]--> Completed | yes | yes (1.0) | Proceeding --[receive 3xx to 6xx / pass response to TU; generate ACK; start TimerD]--> Completed | Same 3xx-6xx handling in Proceeding |
| Proceeding --[receive 1xx / send 1xx to TU]--> Proceeding | yes | yes (1.0) | Proceeding --[receive 1xx / pass response to TU]--> Proceeding | Same 1xx in Proceeding |
| Proceeding --[receive INVITE / send response]--> Proceeding | no | yes (0.75) | Proceeding --[receive RequestRetransmit / retransmit last response]--> Proceeding | INVITE retransmit in Proceeding triggers response retransmission - server INVITE transaction behavio |
| Proceeding --[receive Transport Error / inform TU]--> Terminated | yes | yes (1.0) | Proceeding --[cond TransportError / inform TU]--> Terminated | Same transport error handling in Proceeding |
| Completed --[timeout Timer D / ]--> Terminated | yes | yes (1.0) | Completed --[timeout TimerD / destroy transaction]--> Terminated | Same Timer D expiry |
| Completed --[receive 300-699 / send ACK]--> Completed | yes | yes (0.7) | Completed --[receive ResponseRetransmit / re-pass ACK to transport]--> Completed | Response retransmit in Completed triggers ACK retransmission - client INVITE transaction |
| Completed --[receive Transport Error / inform TU]--> Terminated | yes | yes (1.0) | Completed --[cond TransportError / inform TU]--> Terminated | Same transport error in Completed |
| Completed --[receive ACK / ]--> Confirmed | yes | yes (1.0) | Completed --[receive ACK / set TimerI]--> Confirmed | Same ACK reception moving to Confirmed |
| Confirmed --[timeout Timer I / ]--> Terminated | yes | yes (1.0) | Confirmed --[timeout TimerI / destroy transaction]--> Terminated | Same Timer I expiry |
| Proceeding --[receive 101-199 from TU / send response]--> Proceeding | no | yes (0.85) | Proceeding --[send Provisional / pass to transport]--> Proceeding | TU sends provisional → transport; server INVITE transaction |
| Proceeding --[receive 300-699 from TU / send response]--> Completed | yes | yes (0.9) | Proceeding --[send 3xx to 6xx / pass to transport; start TimerG TimerH]--> Completed | TU sends final non-2xx response; server INVITE transaction |
| Completed --[timeout Timer G / send response]--> Completed | yes | yes (1.0) | Completed --[timeout TimerG / retransmit response]--> Completed | Same Timer G retransmission |
| Completed --[receive INVITE / send response]--> Completed | no | yes (0.8) | Completed --[receive RequestRetransmit / retransmit response]--> Completed | INVITE retransmit in Completed triggers response retransmission - server side |
| Completed --[timeout Timer H or Transport Error / inform TU]--> Terminated | yes | yes (0.8) | Completed --[timeout TimerH / inform TU]--> Terminated | Timer H part matches; transport error is separate in candidate |

Our unmatched transitions, judge classification: real behaviour not in GT 16, duplicate/alias 1, wrong/unsupported 28.

## RTSP — states: string 3/3, meaning 3/3 | transitions: string 12/33, meaning 24/33 (both 12) | ours: 4 states, 30 transitions

| GT state | string match (cos) | meaning match (conf) | judge reason |
|---|---|---|---|
| Init | Init (1.0) | Init (1.0) | Both represent the initial state before session setup |
| Ready | Ready (1.0) | Ready (1.0) | Both represent the state after SETUP, before PLAY |
| Play | Play (1.0) | Play (1.0) | Both represent active media playback state |

| GT transition | string | meaning | our matching edge (by meaning) | judge reason |
|---|---|---|---|---|
| Init --[cond default / send SETUP; reply NRM=1, RP=0.0]--> Ready | no | yes (0.9) | Init --[receive SETUP success / generate session identifier; set transport parameters]--> Ready | Both represent successful SETUP transitioning Init->Ready |
| Init --[cond Needs Redirect / send SETUP; reply 3rr Redirect]--> Init | yes | yes (0.95) | Init --[receive SETUP redirect / send 3rr redirect response]--> Init | Both represent SETUP requiring redirect, staying in Init |
| Init --[cond No Session hdr / send S -> C: REDIRECT; reply Terminate all SES]--> Init | yes | yes (0.85) | Init --[receive REDIRECT / terminate all sessions]--> Init | Both represent REDIRECT terminating all sessions in Init |
| Ready --[cond New URI / send SETUP; reply NRM +=1]--> Ready | no | no | — | No candidate transition specifically matches adding a new URI with NRM increment (candidate index 3  |
| Ready --[cond URI Setup prior / send SETUP; reply Change transport param]--> Ready | yes | yes (0.8) | Ready --[receive SETUP / update transport parameters]--> Ready | Both represent SETUP changing transport parameters in Ready |
| Ready --[cond Prs URI / send TEARDOWN; reply No session hdr, NRM = 0]--> Init | yes | yes (0.85) | Ready --[receive TEARDOWN session / destroy session state]--> Init | Both represent session-level TEARDOWN from Ready to Init |
| Ready --[cond md URI,NRM=1 / send TEARDOWN; reply No Session hdr, NRM = 0]--> Init | no | no | — | Candidate index 8 already matched to gt_index 5; no separate match for last-media TEARDOWN from Read |
| Ready --[cond md URI,NRM>1 / send TEARDOWN; reply Session hdr, NRM -= 1]--> Ready | no | yes (0.8) | Ready --[receive TEARDOWN media / remove media stream; free associated resources]--> Ready | Both represent TEARDOWN of a media URI when NRM>1, staying in Ready |
| Ready --[cond Prs URI, No range / send PLAY; reply Play from RP]--> Play | no | no | — | Candidate index 7 already matched to gt_index 9; no separate PLAY without range |
| Ready --[cond Prs URI, Range / send PLAY; reply According to range]--> Play | no | yes (0.75) | Ready --[receive PLAY / begin media delivery]--> Play | Both represent PLAY transitioning Ready->Play |
| Ready --[cond md URI, NRM=1, Range / send PLAY; reply According to range]--> Play | no | no | — | No separate candidate for aggregate PLAY with range from Ready |
| Ready --[cond md URI, NRM=1 / send PLAY; reply Play from RP]--> Play | no | no | — | No separate candidate for aggregate PLAY without range from Ready |
| Ready --[cond Prs URI / send PAUSE; reply Return PP]--> Ready | yes | yes (0.85) | Ready --[receive PAUSE / send 200 OK response]--> Ready | Both represent PAUSE in Ready state |
| Ready --[cond Terminate-Reason / send SC:REDIRECT; reply Set RedP]--> Ready | no | yes (0.9) | Ready --[receive REDIRECT timed / set redirect deadline]--> Ready | Both represent timed REDIRECT setting a redirect point in Ready |
| Ready --[cond No Terminate-Reason time parameter / send SC:REDIRECT; reply Session is removed]--> Init | yes | yes (0.9) | Ready --[receive REDIRECT immediate / remove session]--> Init | Both represent immediate REDIRECT removing session |
| Ready --[cond default / timeout]--> Init | no | yes (0.95) | Ready --[timeout session / remove session]--> Init | Both represent session timeout in Ready going to Init |
| Ready --[cond default / cond RedP reached; reply TEARDOWN of session]--> Init | no | yes (0.95) | Ready --[cond redirect deadline / teardown session]--> Init | Both represent redirect point reached causing teardown |
| Play --[cond Prs URI / send PAUSE; reply Set RP to present point]--> Ready | no | yes (0.9) | Play --[receive PAUSE / halt media delivery]--> Ready | Both represent PAUSE transitioning Play->Ready |
| Play --[cond All media / set Set RP = End of media]--> Play | yes | yes (0.9) | Play --[cond end of media / set resume point to end]--> Play | Both represent end-of-media setting resume point |
| Play --[cond default / set Set RP = End of range]--> Play | no | yes (0.75) | Play --[cond end of range / stop media delivery]--> Play | Both represent end-of-range condition in Play state |
| Play --[cond Prs URI, No range / send PLAY; reply Play from present point]--> Play | no | no | — | Candidate index 13 already matched to gt_index 21 |
| Play --[cond Prs URI, Range / send PLAY; reply According to range]--> Play | no | yes (0.7) | Play --[receive PLAY / replace current play action]--> Play | Both represent PLAY in Play state staying in Play |
| Play --[cond default / send SC:PLAY_NOTIFY; reply 200]--> Play | yes | yes (0.85) | Play --[receive PLAY NOTIFY / send 200 response]--> Play | Both represent PLAY_NOTIFY with 200 response in Play |
| Play --[cond New URI / send SETUP; reply 455]--> Play | no | no | — | No candidate for SETUP with new URI returning 455 error in Play |
| Play --[cond md URI / send SETUP; reply 455]--> Play | no | no | — | No candidate for SETUP with media URI returning 455 in Play |
| Play --[cond md URI, IFI / send SETUP; reply Change transport param.]--> Play | no | yes (0.85) | Play --[receive SETUP / change transport parameters]--> Play | Both represent SETUP changing transport params in Play |
| Play --[cond Prs URI / send TEARDOWN; reply No session hdr]--> Init | yes | yes (0.85) | Play --[receive TEARDOWN session / destroy session state]--> Init | Both represent session TEARDOWN from Play to Init |
| Play --[cond md URI,NRM=1 / send TEARDOWN; reply No Session hdr, NRM=0]--> Init | no | yes (0.7) | Play --[receive TEARDOWN affected / terminate media session]--> Init | Both represent TEARDOWN of last media URI from Play to Init |
| Play --[cond md URI / send TEARDOWN; reply 455]--> Play | no | no | — | No candidate for TEARDOWN returning 455 staying in Play |
| Play --[cond Terminate Reason with Time parameter / send SC:REDIRECT; reply Set RedP]--> Play | yes | yes (0.9) | Play --[receive REDIRECT timed / set redirect deadline]--> Play | Both represent timed REDIRECT in Play state |
| Play --[cond default / send SC:REDIRECT; reply Session is removed]--> Init | yes | yes (0.9) | Play --[receive REDIRECT immediate / remove session]--> Init | Both represent immediate REDIRECT removing session from Play |
| Play --[cond default / cond RedP reached; reply TEARDOWN of session]--> Init | no | yes (0.95) | Play --[cond redirect deadline / teardown session]--> Init | Both represent redirect deadline reached causing teardown from Play |
| Play --[cond default / timeout; reply Stop Media playout]--> Init | yes | yes (0.95) | Play --[timeout session / stop media playout]--> Init | Both represent session timeout stopping media in Play |

Our unmatched transitions, judge classification: real behaviour not in GT 2, duplicate/alias 2, wrong/unsupported 2.

## MQTT — states: string 12/12, meaning 10/12 | transitions: string 4/17, meaning 8/17 (both 3) | ours: 37 states, 57 transitions

| GT state | string match (cos) | meaning match (conf) | judge reason |
|---|---|---|---|
| Disconnected | Disconnected (1.0) | NewSession (0.7) | Both are the initial state before any connection/token activity |
| Waiting for Token | TokenUploaded (0.55) | — | No candidate state clearly represents waiting for token request |
| Token Requested | TokenUploaded (0.66) | — | No candidate state represents the pending token request phase |
| Token Received | TokenObtained (0.63) | TokenObtained (0.95) | Both represent having received/obtained the access token from AS |
| Awaiting CONNACK | AwaitingConnack (0.8) | WaitingForConnack (0.9) | Both represent waiting for CONNACK from broker |
| Authenticating | Authenticating (1.0) | Connecting (0.6) | Both represent the phase where connection request with token is being processed |
| Token Validating | TokenValidation (0.83) | TokenValidation (0.95) | Both represent broker validating the token |
| Connected | Connected (1.0) | Connected (1.0) | Same name and meaning - active MQTT session |
| Session Resumed | SessionMaintained (0.7) | ExistingSessionContinued (0.85) | Both represent a resumed/continued prior session |
| Unauthorized | Authorizing (0.57) | ValidationFailed (0.85) | Both represent failed token validation / unauthorized state |
| Reauthenticating | Reauthenticating (1.0) | Reauthenticating (1.0) | Same name and meaning - reauthentication in progress |
| Session Terminated | SessionMaintained (0.69) | Disconnected (0.75) | Candidate's Disconnected is a final state representing session end |

| GT transition | string | meaning | our matching edge (by meaning) | judge reason |
|---|---|---|---|---|
| Disconnected --[Client initiates connection / Start TLS handshake]--> Waiting for Token | no | no | — | No candidate transition from NewSession involves TLS handshake to a waiting-for-token state |
| Waiting for Token --[Request token from AS / Send token request (HTTPS/PSK/RPK)]--> Token Requested | no | no | — | No matching states for Waiting for Token or Token Requested |
| Token Requested --[Token received from AS / Store JWT/CWT token]--> Token Received | no | yes (0.7) | NewSession --[receive access token / receive access token from AS]--> TokenObtained | Both represent receiving token from AS, though source states differ due to GT having intermediate st |
| Token Requested --[Token request timeout/error / Retry or terminate]--> Disconnected | yes | no | — | No candidate transition represents token request failure |
| Token Received --[Send CONNECT/AUTH packet / Include token in 'authz-info' topic]--> Authenticating | no | yes (0.8) | TokenObtained --[send connection request / send connection request with token]--> Connecting | Both send connection request with token from token-received state to authenticating/connecting state |
| Authenticating --[Token uploaded / Wait for broker response]--> Awaiting CONNACK | no | no | — | Candidate's Connecting→Connected skips the awaiting CONNACK phase; no direct match |
| Awaiting CONNACK --[Broker processes token / Validate token (introspection)]--> Token Validating | no | no | — | Candidate has introspection from Connecting state, not from WaitingForConnack to TokenValidation |
| Token Validating --[Token valid / Send CONNACK(Success)]--> Connected | yes | yes (0.95) | TokenValidation --[cond token valid / send CONNACK reason_code 0x00]--> Connected | Both: token valid → send CONNACK success → Connected |
| Token Validating --[Token invalid/expired / Send CONNACK(Not Authorized)]--> Unauthorized | yes | yes (0.8) | TokenValidation --[cond token expired or invalid / check token expiry; check token audience; check issuer authorization]--> ValidationFailed | Both: invalid/expired token from validation state to unauthorized/failed state |
| Connected --[CONNECT(CleanSession=0) / Resume prior session]--> Session Resumed | no | no | — | Candidate has CurrentSession→ExistingSessionContinued but Connected is not matched to CurrentSession |
| Session Resumed --[Session validated / Send CONNACK(Session Present=1)]--> Connected | no | yes (0.85) | ExistingSessionContinued --[cond session resumed / send CONNACK SessionPresent 1]--> Connected | Both: resumed session validated → CONNACK SessionPresent=1 → Connected |
| Session Resumed --[Session invalid / Send DISCONNECT]--> Session Terminated | no | no | — | No candidate transition from ExistingSessionContinued to a terminated/disconnected state |
| Connected --[Token expired / Send AUTH packet]--> Reauthenticating | yes | yes (0.85) | Connected --[send AUTH reauth / send AUTH 0x19 Reauthentication; set auth method ace; transport new token]--> Reauthenticating | Both: Connected → send AUTH → Reauthenticating |
| Reauthenticating --[Broker validates new token / Process AUTH/CONNACK]--> Token Validating | no | no | — | No candidate transition from Reauthenticating to TokenValidation |
| Reauthenticating --[Reauthentication failed / Send DISCONNECT]--> Unauthorized | no | yes (0.8) | Reauthenticating --[cond reauth failed / send DISCONNECT 0x87]--> Disconnected | Both: reauth failed → DISCONNECT. Destination states differ in name but both represent session end |
| Connected --[DISCONNECT received/sent / Cleanup session]--> Session Terminated | no | yes (0.8) | Connected --[send CLIENT DISCONNECT / discard Session State; keep retained messages]--> Disconnected | Both: Connected → DISCONNECT → session terminated/disconnected |
| Unauthorized --[DISCONNECT sent / Close connection]--> Session Terminated | no | no | — | No explicit transition from ValidationFailed to Disconnected/Closed in candidate |

Our unmatched transitions, judge classification: real behaviour not in GT 38, duplicate/alias 10, wrong/unsupported 0.

## SMTP — states: string 5/7, meaning 6/7 | transitions: string 1/22, meaning 9/22 (both 1) | ours: 13 states, 38 transitions

| GT state | string match (cos) | meaning match (conf) | judge reason |
|---|---|---|---|
| Connection Established | ConnectionOpened (0.71) | Greeting (0.6) | After TCP connection opened and 220 sent, waiting for EHLO/HELO - maps to the state before greeting exchange c |
| Greeted | Greeting (0.64) | SessionInitialized (0.95) | Both represent state after successful EHLO/HELO, ready for MAIL FROM |
| Mail Transaction Started | MailTransaction (0.61) | MailTransaction (0.8) | Both represent state after MAIL FROM accepted; candidate merges RCPT phase too |
| Recipient Specified | — | — | Candidate merges this into MailTransaction, no separate state |
| Data Entry | DataTransfer (0.52) | DataTransfer (0.95) | Both represent the data transfer phase after DATA/354 |
| Message Accepted | — | MailAccepted (0.7) | Both represent post-message-acceptance, though candidate focuses on delivery responsibility |
| Session Terminated | SessionRejected (0.61) | ConnectionClosed (0.95) | Both represent the session/connection being closed |

| GT transition | string | meaning | our matching edge (by meaning) | judge reason |
|---|---|---|---|---|
| Connection Established --[receive EHLO or HELO after 220 greeting / send HELO]--> Greeted | yes | yes (0.7) | Greeting --[receive EHLO accepted / clear all state tables and buffers]--> SessionInitialized | Both transition from initial state to greeted/initialized after EHLO accepted |
| Greeted --[receive MAIL FROM / send MAIL FROM]--> Mail Transaction Started | no | yes (0.85) | SessionInitialized --[receive MAIL accepted / clear buffers and insert reverse-path]--> MailTransaction | Both transition from greeted to mail transaction on MAIL command |
| Mail Transaction Started --[receive RCPT TO / send RCPT TO]--> Recipient Specified | no | no | — | Candidate has no separate Recipient Specified state; RCPT stays in MailTransaction |
| Recipient Specified --[receive DATA / send DATA 354]--> Data Entry | no | yes (0.75) | MailTransaction --[receive DATA accepted / send 354 intermediate reply]--> DataTransfer | Both transition to data transfer on DATA command with 354 reply; source states differ but semantical |
| Data Entry --[cond message ended with <CRLF>.<CRLF> / send message body]--> Message Accepted | no | yes (0.65) | DataTransfer --[send 250 data accepted / accept delivery responsibility]--> MailAccepted | Both represent successful completion of data transfer leading to message accepted |
| Message Accepted --[receive QUIT / send QUIT]--> Session Terminated | no | no | — | No QUIT transition from MailAccepted in candidate |
| Greeted --[receive RSET / send RSET]--> Greeted | no | yes (0.9) | SessionInitialized --[receive RSET / send 250 OK reply]--> SessionInitialized | Both are RSET self-loop in greeted/initialized state |
| Mail Transaction Started --[receive RSET / send RSET]--> Greeted | no | yes (0.9) | MailTransaction --[receive RSET / abort transaction and clear buffers]--> SessionInitialized | Both reset from mail transaction back to greeted/initialized |
| Recipient Specified --[receive RSET / send RSET]--> Greeted | no | no | — | No separate Recipient Specified state in candidate |
| Data Entry --[receive RSET before message is complete / send RSET]--> Greeted | no | no | — | No RSET from DataTransfer in candidate |
| Message Accepted --[receive RSET / send RSET]--> Greeted | no | no | — | No RSET from MailAccepted in candidate |
| Greeted --[receive VRFY / send VRFY]--> Greeted | no | no | — | No VRFY transitions in candidate |
| Greeted --[receive EXPN / send EXPN; reply 250]--> Greeted | no | no | — | No EXPN transitions in candidate |
| Mail Transaction Started --[receive VRFY or EXPN / send VRFY; send EXPN]--> Mail Transaction Started | no | no | — | No VRFY/EXPN in candidate |
| Recipient Specified --[receive VRFY or EXPN / send VRFY; send EXPN]--> Recipient Specified | no | no | — | No VRFY/EXPN in candidate |
| Data Entry --[receive VRFY or EXPN / send VRFY; send EXPN]--> Data Entry | no | no | — | No VRFY/EXPN in candidate |
| Message Accepted --[receive VRFY or EXPN / send VRFY; send EXPN]--> Message Accepted | no | no | — | No VRFY/EXPN in candidate |
| Connection Established --[receive QUIT / send QUIT]--> Session Terminated | no | yes (0.7) | Greeting --[receive QUIT / send 221 and close connection]--> ConnectionClosed | Both handle QUIT from initial/greeting state to closed |
| Greeted --[receive QUIT / send QUIT]--> Session Terminated | no | yes (0.9) | SessionInitialized --[receive QUIT / send 221 and close connection]--> ConnectionClosed | Both handle QUIT from greeted/initialized to closed |
| Mail Transaction Started --[receive QUIT / send QUIT]--> Session Terminated | no | yes (0.85) | MailTransaction --[receive QUIT / abort current transaction]--> ConnectionClosed | Both handle QUIT from mail transaction to closed |
| Recipient Specified --[receive QUIT / send QUIT]--> Session Terminated | no | no | — | No separate Recipient Specified state in candidate |
| Data Entry --[receive QUIT before finishing data / send QUIT]--> Session Terminated | no | no | — | No QUIT from DataTransfer in candidate |

Our unmatched transitions, judge classification: real behaviour not in GT 28, duplicate/alias 1, wrong/unsupported 0.

## NNTP — states: string 7/10, meaning 6/10 | transitions: string 0/16, meaning 6/16 (both 0) | ours: 27 states, 60 transitions

| GT state | string match (cos) | meaning match (conf) | judge reason |
|---|---|---|---|
| Initial_Connection | — | Established (0.95) | Both represent the initial state upon TCP connection before greeting is processed. |
| Service_Available | — | — | Service_Available after receiving 200 could map to TransitMode (server in transit/general mode before MODE REA |
| Connection_Closed | Closed (0.67) | Closed (1.0) | Both represent the terminal closed connection state. |
| Posting_Allowed | PostingAllowed (0.63) | PostingAllowed (0.75) | Both represent a state where posting is allowed. In GT it's reached after MODE READER; in candidate it's reach |
| Posting_Prohibited | PostingAllowed (0.63) | PostingProhibited (0.85) | Both represent a state where posting is prohibited, reached after 201 greeting. |
| Group_Selected | GroupSelected (0.71) | GroupSelected (0.95) | Both represent the state where a newsgroup has been selected. |
| Article_Selected | ArticleInput (0.52) | — | No candidate state corresponds to a state after fetching an article but before reading headers/body. |
| Reading_Headers | — | — | No candidate state corresponds to reading article headers. |
| Reading_Body | ReadingMode (0.57) | — | No candidate state corresponds to reading article body. |
| Posting_Article | PostingAllowed (0.5) | ArticleInput (0.85) | Both represent the state where an article is being submitted/posted, awaiting the result (240 or 441). |

| GT transition | string | meaning | our matching edge (by meaning) | judge reason |
|---|---|---|---|---|
| Initial_Connection --[Receive 200 / Set service available]--> Service_Available | no | yes (0.6) | Established --[receive 200 greeting / send 200 response]--> PostingAllowed | Both transition from initial state on receiving 200 greeting. Destination states differ in naming bu |
| Initial_Connection --[Receive 201 / Set posting prohibited]--> Posting_Prohibited | no | yes (0.95) | Established --[receive 201 greeting / send 201 response]--> PostingProhibited | Both transition from initial state on receiving 201 to a posting-prohibited state. |
| Initial_Connection --[Receive 502 / Close connection]--> Connection_Closed | no | yes (0.95) | Established --[receive 502 greeting / send 502 response; close connection immediately]--> Closed | Both transition from initial state on 502 to closed. |
| Initial_Connection --[Receive 400 / Close connection]--> Connection_Closed | no | yes (0.95) | Established --[receive 400 greeting / send 400 response; close connection]--> Closed | Both transition from initial state on 400 to closed. |
| Service_Available --[MODE READER / Switch to reader mode]--> Posting_Allowed | no | no | — | Both involve MODE READER from a general service state to a reader/posting-allowed state. But the sta |
| Posting_Prohibited --[MODE READER / Switch to reader mode]--> Posting_Allowed | no | no | — | No candidate transition from PostingProhibited on MODE READER to a posting-allowed state. |
| Posting_Allowed --[GROUP <group_name> / Select newsgroup]--> Group_Selected | no | no | — | Both select a newsgroup and transition to GroupSelected. Source states differ (PostingAllowed vs NoG |
| Group_Selected --[ARTICLE <article_id> / Fetch article]--> Article_Selected | no | no | — | No candidate transition for ARTICLE command from GroupSelected to an Article_Selected state. |
| Group_Selected --[POST / Start article posting]--> Posting_Article | no | no | — | Both initiate POST and transition to an article-input/posting state. Source states differ (GroupSele |
| Article_Selected --[HEAD <article_id> / Read headers]--> Reading_Headers | no | no | — | No candidate state or transition for HEAD command. |
| Article_Selected --[BODY <article_id> / Read body]--> Reading_Body | no | no | — | No candidate state or transition for BODY command. |
| Reading_Headers --[QUIT / Close connection]--> Connection_Closed | no | no | — | No Reading_Headers state in candidate. |
| Reading_Body --[QUIT / Close connection]--> Connection_Closed | no | no | — | No Reading_Body state in candidate. |
| Posting_Article --[Receive 240 / Article posted successfully]--> Group_Selected | no | yes (0.7) | ArticleInput --[receive article success / send 240 response]--> PostingAllowed | Both represent successful article posting (240). Source states match (Posting_Article~ArticleInput). |
| Posting_Article --[Receive 441 / Posting failed]--> Group_Selected | no | yes (0.7) | ArticleInput --[receive article fail / send 441 response]--> PostingAllowed | Both represent failed article posting (441). Source states match. Destination differs but semantical |
| Group_Selected --[QUIT / Close connection]--> Connection_Closed | no | no | — | No direct QUIT transition from GroupSelected in candidate. The candidate has QUIT from CommandExchan |

Our unmatched transitions, judge classification: real behaviour not in GT 48, duplicate/alias 2, wrong/unsupported 2.

## FTP — states: string 8/10, meaning 8/10 | transitions: string 2/24, meaning 13/24 (both 1) | ours: 24 states, 52 transitions

| GT state | string match (cos) | meaning match (conf) | judge reason |
|---|---|---|---|
| Disconnected | Closed (0.6) | AwaitingInput (0.7) | Both are the state where USER command is received to begin login |
| Connected | Closed (0.55) | NeedPassword (0.85) | Both await PASS after USER/331 |
| Authenticated | LoggedIn (0.52) | LoggedIn (0.95) | Both represent fully logged-in state |
| Need_Account | NeedAccount (0.61) | NeedAccount (0.95) | Both represent needing ACCT |
| Accounted | — | — | No separate post-ACCT state in candidate; merges into LoggedIn |
| Data_Connection_Ready | DataConnClosed (0.58) | — | Partial match: only covers PASV, not PORT |
| Data_Transfer | DataTransfer (0.57) | DataTransfer (0.95) | Both represent active data transfer |
| Restart_Point_Set | RestartPending (0.63) | RestartPending (0.95) | Both represent REST marker set, awaiting transfer command |
| Awaiting_RNTO | — | RenamePending (0.95) | Both represent RNFR sent, awaiting RNTO |
| Logged_Out | LoggedIn (0.79) | Closed (0.9) | Both represent session termination |

| GT transition | string | meaning | our matching edge (by meaning) | judge reason |
|---|---|---|---|---|
| Disconnected --[receive USER / reply 331]--> Connected | no | yes (0.9) | AwaitingInput --[receive USER 331 / send 331 need password]--> NeedPassword | USER→331→NeedPassword matches USER→331→Connected |
| Connected --[receive PASS / reply 230]--> Authenticated | no | yes (0.95) | NeedPassword --[receive PASS 230 / send 230 user logged in]--> LoggedIn | PASS→230→LoggedIn matches |
| Connected --[receive PASS / reply 530]--> Disconnected | no | yes (0.9) | NeedPassword --[receive PASS 530 / send 530 login failed]--> AwaitingInput | PASS→530→back to initial login state |
| Connected --[receive PASS / reply 332]--> Need_Account | no | yes (0.95) | NeedPassword --[receive PASS 332 / send 332 need account]--> NeedAccount | PASS→332→NeedAccount matches |
| Need_Account --[receive ACCT / reply 230]--> Accounted | no | yes (0.85) | NeedAccount --[receive ACCT 230 / send 230 user logged in]--> LoggedIn | ACCT→230→LoggedIn; Accounted merges into LoggedIn |
| Need_Account --[receive ACCT / reply 332]--> Need_Account | yes | no | — | No self-loop ACCT→332 on NeedAccount in candidate |
| Authenticated --[receive ACCT / reply 230]--> Accounted | no | no | — | No ACCT transition from LoggedIn in candidate |
| Authenticated --[receive ACCT / reply 332]--> Authenticated | no | no | — | No ACCT self-loop on LoggedIn in candidate |
| Authenticated --[receive PASV / reply 227]--> Data_Connection_Ready | no | yes (0.9) | LoggedIn --[receive PASV 227 / send 227 entering passive]--> EnteringPassiveMode | PASV→227 matches |
| Authenticated --[receive PORT / reply 200]--> Data_Connection_Ready | no | no | — | No PORT transition from LoggedIn in candidate |
| Data_Connection_Ready --[receive RETR / reply 150]--> Data_Transfer | no | no | — | No RETR from EnteringPassiveMode; candidate uses generic service command from LoggedIn |
| Data_Connection_Ready --[receive STOR / reply 150]--> Data_Transfer | no | no | — | No STOR from EnteringPassiveMode |
| Data_Connection_Ready --[receive APPE / reply 150]--> Data_Transfer | no | no | — | No APPE from EnteringPassiveMode |
| Data_Transfer --[Transfer Complete / reply 226]--> Authenticated | no | yes (0.75) | DataTransfer --[receive reply 2yz / close data connection]--> LoggedIn | Transfer complete→2xx→LoggedIn matches semantically |
| Data_Transfer --[receive QUIT / reply 221]--> Logged_Out | no | yes (0.9) | DataTransfer --[receive QUIT / close connection after result]--> Closed | QUIT during transfer→Closed matches |
| Authenticated --[receive REST / reply 350]--> Restart_Point_Set | yes | yes (0.95) | LoggedIn --[receive REST 350 / send 350 restart marker]--> RestartPending | REST→350→RestartPending matches |
| Restart_Point_Set --[receive RETR / reply 150]--> Data_Transfer | no | yes (0.6) | RestartPending --[send transfer command / send transfer command]--> DataTransfer | Generic transfer command from RestartPending→DataTransfer; RETR is one such command |
| Restart_Point_Set --[receive STOR / reply 150]--> Data_Transfer | no | no | — | Already matched candidate 25 to RETR; no separate STOR transition |
| Restart_Point_Set --[receive APPE / reply 150]--> Data_Transfer | no | no | — | No separate APPE from RestartPending |
| Authenticated --[receive RNFR / reply 350]--> Awaiting_RNTO | no | yes (0.95) | LoggedIn --[receive RNFR 350 / send 350 pending info]--> RenamePending | RNFR→350→RenamePending matches |
| Awaiting_RNTO --[receive RNTO / reply 250]--> Authenticated | no | yes (0.95) | RenamePending --[receive RNTO 250 / rename file send 250]--> LoggedIn | RNTO→250→LoggedIn matches |
| Authenticated --[receive QUIT / reply 221]--> Logged_Out | no | yes (0.9) | LoggedIn --[receive QUIT / close control connection]--> Closed | QUIT→Closed matches |
| Accounted --[receive QUIT / reply 221]--> Logged_Out | no | no | — | No Accounted state in candidate |
| Restart_Point_Set --[receive QUIT / reply 221]--> Logged_Out | no | no | — | No QUIT from RestartPending in candidate |

Our unmatched transitions, judge classification: real behaviour not in GT 24, duplicate/alias 0, wrong/unsupported 15.
