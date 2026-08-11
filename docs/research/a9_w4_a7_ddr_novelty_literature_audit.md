# A9 W4: A7 Event-Triggered DDR Address-Link Literature Audit

## Scope and evidence boundary

This audit was performed on 2026-08-11 for A7 commit
`db3f04fe0e01699e63c596145fe71effc601e57c` (`db3f04f`). That is the latest
reviewed A7 commit. Commit `a349d64d8b8b3d4398a258926af493b5da1e3ac2`
is a structural-evidence ancestor, not the latest implementation. The older A9
W4 analytical tournament remains intentionally frozen to pre-ICG A7 commit
`31947a71ddfcf678f6cd593954df34b27806a63d`; its 12-bit link-state and activity
numbers must not be relabeled as `db3f04f` results.

The reviewed `db3f04f` design is an address-only N=16 link. It holds a four-bit
event address, places bits `[1:0]` and `[3:2]` on two data wires on opposite
edges of a conditionally forwarded clock, and commits one reconstructed address
at the receiver. Two data wires plus the forwarded clock are three link
signals, excluding reset and supplies. An explicit low-transparent ICG boundary
gates the burst clock. The RTL has no FIFO, backpressure, arbitration,
clock-domain synchronizer, runtime framing detector, or error recovery. Its
strict protocol oracle is test-only.

This is a focused literature search, not a patent search, freedom-to-operate
opinion, or proof of novelty/non-obviousness. In particular, failure to find an
identical circuit is not evidence that none exists.

## Primary prior-art map

| Established idea | Primary source | What it prevents A7 from claiming | Relevant difference in `db3f04f` |
|---|---|---|---|
| Address-event virtual wiring and time-multiplexed event identities | Boahen, 2000, [DOI 10.1109/82.842110](https://doi.org/10.1109/82.842110) | First AER link, first address-only event identity, or first time-multiplexed neuromorphic bus | A7 fixes the identity to four source/address bits and studies a particular physical framing |
| Burst-mode word-serial AER transmitter | Boahen, 2004, [DOI 10.1109/TCSI.2004.830703](https://doi.org/10.1109/TCSI.2004.830703) | First word-serial AER, first sequential address partition, first burst-mode pad reduction | A7 sends two two-bit symbols on opposite clock edges rather than asynchronous row/column words |
| Matching word-serial receiver | Boahen, 2004, [DOI 10.1109/TCSI.2004.830702](https://doi.org/10.1109/TCSI.2004.830702) | First receiver to reconstruct an event from sequential address components | A7 has a fixed two-symbol, one-DDR-period receive rule and falling-edge commit |
| Fabricated burst-link analysis and test | Boahen, 2004, [DOI 10.1109/TCSI.2004.830701](https://doi.org/10.1109/TCSI.2004.830701) | First demonstrated burst AER or general latency/capacity benefit from address serialization | A7 currently has RTL/generic structural evidence, not equivalent silicon evidence |
| Delay-insensitive encoded address-event transport | Lin and Boahen, 2009, [DOI 10.1109/ASYNC.2009.25](https://doi.org/10.1109/ASYNC.2009.25) | First asynchronous or coding-robust AER channel | A7 is synchronous bundled data with a forwarded clock and does not provide delay-insensitive coding |
| Serial AER over a flow-controlled physical link | Fasnacht, Whatley, and Indiveri, 2008, [DOI 10.1109/ISCAS.2008.4541501](https://doi.org/10.1109/ISCAS.2008.4541501) | First serial AER transport or first serial flow-controlled event infrastructure | A7 is narrower and fixed-frame, but has no flow control |
| High-speed bit-serial bidirectional AER with alignment, correction, and flow control | Yousefzadeh et al., 2017, [DOI 10.1109/TBCAS.2017.2717341](https://doi.org/10.1109/TBCAS.2017.2717341) | First high-speed serial AER, first reliable serial event link, or first multi-channel link | A7 has none of the cited reliability mechanisms and makes no BER claim |
| Synchronous serial AER ring | Dorta et al., 2016, [DOI 10.1016/j.neucom.2015.07.080](https://doi.org/10.1016/j.neucom.2015.07.080) | First synchronous serial AER or first serial topology for spike distribution | A7 is a point-to-point bare frame, not a routed/packetized ring |
| Clockless, event-driven, self-sleeping bit-serial AER link | Qiao and Indiveri, 2018, [DOI 10.1109/ASYNC.2018.00028](https://doi.org/10.1109/ASYNC.2018.00028) | First sparse-event wake/sleep serial AER, first clockless serial AER, or first minimal-pin low-power event link | A7 uses a synchronous event-gated forwarded clock and has no measured link-power result |
| Source-synchronous DDR with a forwarded clock and both-edge transfer | Gui et al., 2005, [DOI 10.1109/TVLSI.2005.850101](https://doi.org/10.1109/TVLSI.2005.850101) | First source-synchronous DDR link, first forwarded-clock dual-edge transfer, or generic bandwidth-doubling claim | A7 applies the established technique to one fixed four-bit AER frame |
| Switching-activity and clock-gating principles for low-power CMOS | Chandrakasan, Sheng, and Brodersen, 1992, [DOI 10.1109/4.126534](https://doi.org/10.1109/4.126534) | Novelty of clock gating or of reducing idle switching as a general method | A7 exposes a candidate-specific replaceable ICG boundary; physical power remains unmeasured |

The strongest overlap is compositional. AER address serialization and burst
links predate A7; source-synchronous DDR and both-edge transfer predate A7; and
event-driven self-sleeping serial AER links also predate A7. Combining those
known principles does not by itself support a claim of a new transport class.

## Claim chart

### Claims that are supportable

The following are narrow implementation or verification statements, not claims
of first invention:

1. At exact commit `db3f04f`, A7 implements a candidate-specific N=16 frame in
   which one four-bit address is represented by two fixed two-bit symbols on
   opposite edges of an event-gated forwarded clock.
2. The design makes the clock-gating technology dependency explicit through a
   replaceable low-transparent ICG boundary and records half-cycle timing,
   reset, CDC, and physical-qualification exclusions rather than treating them
   as solved.
3. The candidate provides lockstep/directed verification and a strict
   independent test-only schedule oracle. It does **not** provide equivalent
   synthesizable fault detection or containment.
4. In the reviewed primary-source set, no paper was found that documents the
   complete exact tuple of N=16/four-bit identity, two data wires plus one
   event-gated forwarded clock, fixed low-bits/rising and high-bits/falling
   mapping, one-address-per-clock reconstruction, and A7's verification/ICG
   packaging. This is only a bounded search observation and must not be
   shortened to “first” or “novel.”
5. Any 5-to-3 signal, state-bit, or generic-cell comparison may be reported only
   as a same-top candidate proxy with its exact commit and accounting boundary.
   It is not silicon PPA, energy, BER, or timing closure.

A safe one-sentence description is:

> A7 `db3f04f` is a compact, auditable N=16 implementation instance that maps a
> four-bit address-only event onto two two-bit DDR symbols with a conditionally
> forwarded clock; it composes established AER serialization, source-synchronous
> DDR, and event-driven clock activation rather than claiming those principles
> as new.

### Claims that are prohibited by the evidence

- “First” AER, address-only event bus, serial/word-serial/bit-serial AER, burst
  AER, split-address transport, source-synchronous link, DDR link, dual-edge
  serialization, forwarded-clock link, clock-gated link, sparse-event wake/sleep
  link, CDR-free link, or pin-reduced event link.
- “First source-synchronous DDR AER” or “fundamentally new link architecture.”
  The present search cannot establish absence of an earlier exact combination,
  and the component techniques are plainly established.
- “Lossless,” “reliable,” “fault-tolerant,” “CDC-safe,” “reset-safe,” “glitch-free
  in silicon,” or “runtime checked.” The candidate has no flow control,
  synchronizer, retransmission, runtime framing detector, or physical cell/STA
  evidence.
- Silicon power, energy/event, BER, PVT, CTS, timing, or area superiority. A
  gated RTL clock and activity proxy are not physical measurements.
- Executed A4-RTL-to-A7-RTL composition. A9 commit `3450ddf` is an analytical
  cycle-model tournament; R=1 is only rate-compatible under its assumed legal
  launch envelope. Reset is absent from that composition model. R=2 and R=4
  remain HOLD because the level-valid A4 boundary can be captured multiple
  times by the faster A7 reference clock without an unimplemented qualifier.

## Bibliography

1. M. A. Sivilotti, *Wiring Considerations in Analog VLSI Systems, with
   Application to Field-Programmable Networks*, Caltech PhD thesis, 1991.
   [DOI 10.7907/STJ4-KH72](https://doi.org/10.7907/STJ4-KH72).
2. K. A. Boahen, “Point-to-point connectivity between neuromorphic chips using
   address events,” *IEEE Transactions on Circuits and Systems II*, 47(5),
   416–434, 2000. [DOI 10.1109/82.842110](https://doi.org/10.1109/82.842110).
3. K. A. Boahen, “A burst-mode word-serial address-event link—I: Transmitter
   design,” *IEEE Transactions on Circuits and Systems I*, 51(7), 1269–1280,
   2004. [DOI 10.1109/TCSI.2004.830703](https://doi.org/10.1109/TCSI.2004.830703).
4. K. A. Boahen, “A burst-mode word-serial address-event link—II: Receiver
   design,” *IEEE Transactions on Circuits and Systems I*, 51(7), 1281–1291,
   2004. [DOI 10.1109/TCSI.2004.830702](https://doi.org/10.1109/TCSI.2004.830702).
5. K. A. Boahen, “A burst-mode word-serial address-event link—III: Analysis and
   test results,” *IEEE Transactions on Circuits and Systems I*, 51(7),
   1292–1300, 2004. [DOI 10.1109/TCSI.2004.830701](https://doi.org/10.1109/TCSI.2004.830701).
6. J. Lin and K. A. Boahen, “A delay-insensitive address-event link,” *15th
   IEEE Symposium on Asynchronous Circuits and Systems*, 55–62, 2009.
   [DOI 10.1109/ASYNC.2009.25](https://doi.org/10.1109/ASYNC.2009.25).
7. D. Fasnacht, A. Whatley, and G. Indiveri, “A serial communication
   infrastructure for multi-chip address event systems,” *ISCAS*, 2008.
   [DOI 10.1109/ISCAS.2008.4541501](https://doi.org/10.1109/ISCAS.2008.4541501).
8. A. Yousefzadeh et al., “On multiple AER handshaking channels over high-speed
   bit-serial bidirectional LVDS links with flow-control and clock-correction on
   commercial FPGAs for scalable neuromorphic systems,” *IEEE Transactions on
   Biomedical Circuits and Systems*, 11(5), 2017.
   [DOI 10.1109/TBCAS.2017.2717341](https://doi.org/10.1109/TBCAS.2017.2717341).
9. T. Dorta et al., “AER-SRT: Scalable spike distribution by means of
   synchronous serial ring topology address event representation,”
   *Neurocomputing*, 171, 1684–1690, 2016.
   [DOI 10.1016/j.neucom.2015.07.080](https://doi.org/10.1016/j.neucom.2015.07.080).
10. N. Qiao and G. Indiveri, “A clock-less ultra-low power bit-serial LVDS link
    for address-event multi-chip systems,” *ASYNC*, 93–101, 2018.
    [DOI 10.1109/ASYNC.2018.00028](https://doi.org/10.1109/ASYNC.2018.00028).
11. P. Gui et al., “A source-synchronous double-data-rate parallel optical
    transceiver IC,” *IEEE Transactions on Very Large Scale Integration Systems*,
    13(7), 833–842, 2005.
    [DOI 10.1109/TVLSI.2005.850101](https://doi.org/10.1109/TVLSI.2005.850101).
12. A. P. Chandrakasan, S. Sheng, and R. W. Brodersen, “Low-power CMOS digital
    design,” *IEEE Journal of Solid-State Circuits*, 27(4), 473–484, 1992.
    [DOI 10.1109/4.126534](https://doi.org/10.1109/4.126534).
