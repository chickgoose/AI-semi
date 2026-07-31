# Design Server Audit

Checked read-only on 2026-07-31 KST through the assigned Digital account.
Credentials and complete license-server configuration are intentionally not
recorded here.

## Host and account

- Hostname: `snu.polaris.09`
- OS: CentOS 7, Linux `3.10.0-1160.el7.x86_64`
- Login shell: `/bin/csh`
- Home: `/home/aiasic26911`
- Home filesystem: approximately 3.4 TB available at the time of inspection
- Environment initializer: `~/control_digi.cshrc`

The initializer must be sourced in each fresh remote shell:

```csh
setenv TERM xterm
source ~/control_digi.cshrc
rehash
```

`TERM=xterm` avoids the server's `tmux-256color` terminal warning.

## Confirmed tools

| Purpose | Tool | Confirmed version |
| --- | --- | --- |
| RTL simulation | Xcelium `xrun` | `23.09-s013` |
| Synthesis | Genus | `23.14-s090_1` |
| Place and route | Innovus | `23.14-s088_1` |
| Static timing | Tempus | `23.14-s089_1` |
| Power integrity/analysis | Voltus | `23.14-s089_1` |

Synopsys `dc_shell`, `vcs`, and `pt_shell` were not found in the configured
path.  Jasper and a `conformal` command were also not found by those exact
names, although the environment file contains a Conformal installation root.

Genus batch startup successfully checked out a `Genus_Synthesis` license and
exited normally.  `genus -version` printed a historical build-expiration
message, but the actual 2026-07-31 license/startup probe succeeded.

## Supplied design data

Only three Digital setup files were present in the account home at inspection:

- `control_digi.cshrc`
- `gsclib045_all_v4.7.tgz` — GPDK045 standard-cell/technology data
- `giolib045_v3.3.tgz` — GPDK045 I/O-cell data

The archives were not extracted during the read-only audit.  The standard-cell
archive contains:

- normal, HVT, LVT, and back-bias cell variants;
- Liberty timing and power models;
- functional Verilog cell models;
- macro and technology LEF;
- Cadence technology files and QRC technology data;
- a `GSCLIB045_user_guide.pdf`.

Recorded archive hashes:

```text
gsclib045_all_v4.7.tgz  fb15a057bc783e6b0b2b223261bb51ca170c27a62d33cb44dd4c91808d498ad1
giolib045_v3.3.tgz      4bebbc571333b396a340dd6f47a365bc012d293392268f523c21eb5dcbdafcdb
```

## Provisional PVT choice

For an initial conservative synthesis comparison, use:

```text
gsclib045_all_v4.7/gsclib045/timing/slow_vdd1v0_basicCells.lib
```

Despite its filename, the Liberty header declares:

- library: `slow_vdd1v0`
- voltage: `0.9 V`
- temperature: `125 C`
- operating condition: `PVT_0P9V_125C`

The library README explicitly says the provided timing data uses 2x2 tables
for tool demonstration rather than high-accuracy 7x7 characterization.  Area,
timing, and power numbers should therefore be used for controlled relative
comparison between designs.

## Not found or not yet confirmed

- Official AER RTL interface or testbench.
- Official synthesis/STA/power Tcl scripts.
- Required clock period and I/O delay assumptions.
- Required PVT corner and threshold-voltage cell set.
- Submission directory, filename convention, or upload procedure.
- Whether first-round scoring uses pre-layout or post-layout results.

These items require an additional competition notice, tutorial, or technical
question.  They cannot be inferred from the server home directory.

## Next server setup

After confirming that archive extraction is allowed, extract the standard-cell
archive under the account home, retain the original archives, and configure all
flow scripts through variables rather than absolute paths.  Do not copy the PDK
or license configuration into Git.
