# MC-WTB Metric V3 protocol rationale

Status: protocol rationale only; no holdout result and no motion-benefit claim

Scope: official UZH DAVIS rotation-labelled sequences, supplied pose, software-side evaluation

Evidence rule: only the official UZH dataset authority and primary event-camera papers cited below support scientific statements

## 1. Purpose and decision boundary

Metric V2 measured same-polarity distance to a preceding raw-event anchor. That
quantity remains valid as a frozen negative experiment, but a raw event is an
asynchronous brightness-change measurement, not a persistent landmark or a
reference-time edge label. Metric V3 therefore evaluates whether the **same
events**, warped into one predeclared reference camera, form a sharper image of
warped events (IWE). It does not use a nearest raw event as truth.

The official UZH dataset defines events as `timestamp x y polarity`, pose as
`timestamp px py pz qx qy qz qw`, and calibration as
`fx fy cx cy k1 k2 p1 p2 k3`. It supplies DAVIS images, events, IMU,
calibration, and motion-capture pose; it labels `shapes_rotation`,
`poster_rotation`, and `boxes_rotation` as rotation with increasing speed in
three different scenes ([official UZH dataset page](https://rpg.ifi.uzh.ch/davis_data.html),
[Mueggler et al., IJRR 2017](https://arxiv.org/pdf/1610.08336.pdf)).

The protocol asks one narrow question:

> Given the UZH calibration and event-time orientation, does MC-WTB's
> orientation-only coordinate warp improve a predeclared event-alignment focus
> objective relative to the sensor-fixed baseline, without hiding timestamp
> misuse, out-of-field projections, missing events, or negative controls?

This is not a pose-estimation challenge. Motion is supplied, as allowed by the
project's staged professor Q&A interpretation. Metric V3 does not alter the
endpoint, codec, RTL, or transport.

## 2. Established practice versus this protocol's choices

| Item | Established by primary source | Metric V3 protocol choice |
|---|---|---|
| Event-time geometry | Events are asynchronous; a continuous trajectory can be evaluated at each event timestamp ([Mueggler et al., RSS 2015](https://rpg.ifi.uzh.ch/docs/RSS15_Mueggler.pdf)). | Interpolate a valid pose at every event time; missing bracket, stale pose, or undeclared clock correction is fail-closed. |
| Common reference | Contrast maximization warps every event to a reference time/view, forms an IWE, and measures its contrast ([Gallego et al., CVPR 2018](https://rpg.ifi.uzh.ch/docs/CVPR18_Gallego.pdf)). | Every arm in a window uses the identical frozen `t_ref`, calibration, raster, event IDs, and rasterization kernel. No arm may select its own best reference. |
| Focus objective | IWE variance is the original contrast-maximization objective. Variance, gradient magnitude, and Laplacian magnitude are among the best-performing focus losses in the authors' comparison ([Gallego & Scaramuzza, RA-L 2017](https://rpg.ifi.uzh.ch/docs/RAL16_Gallego.pdf), [Gallego et al., CVPR 2019](https://openaccess.thecvf.com/content_CVPR_2019/papers/Gallego_Focus_Is_All_You_Need_Loss_Functions_for_Event-Based_Vision_CVPR_2019_paper.pdf)). | Unsigned bilinear-IWE variance is the single primary metric. Any smoothing kernel and border rule are frozen on development data before holdout. Gradient/Laplacian scores, if retained, are labelled secondary and cannot replace a failed primary. |
| Timestamp objective | Motion-compensated optical-flow work forms polarity-separated average-timestamp images with bilinear interpolation and minimizes forward/backward timestamp loss ([Zhu et al., ECCV Workshops 2018](https://openaccess.thecvf.com/content_ECCVW_2018/papers/11134/Zhu_Unsupervised_Event-based_Optical_Flow_using_Motion_Compensation_ECCVW_2018_paper.pdf)). | Timestamp consistency is reported separately as a secondary diagnostic. It is never added to focus with a tunable weight. |
| Rasterization | Nearest-pixel accumulation is described as crude and aliasing-prone; bilinear voting over four pixels is used in the primary rotational-motion work ([Gallego & Scaramuzza, RA-L 2017](https://rpg.ifi.uzh.ch/docs/RAL16_Gallego.pdf)). | Use one frozen bilinear splat implementation for every arm; never round first or clamp OOF coordinates onto the border. |
| Rotation model | A calibrated pure-rotation warp is depth-independent; translation cannot generally be explained by rotation ([Gallego & Scaramuzza, RA-L 2017](https://rpg.ifi.uzh.ch/docs/RAL16_Gallego.pdf)). General 6-DoF transfer requires scene depth/plane information ([Gallego et al., CVPR 2018](https://rpg.ifi.uzh.ch/docs/CVPR18_Gallego.pdf)). | Apply only relative orientation. Preserve translation in provenance but do not apply or silently approximate it. The claim is rotation-only even though the official sequence label says “rotation.” |
| Development/holdout | UZH provides rotation-labelled simple-shape, poster, and highly textured scenes ([official UZH dataset page](https://rpg.ifi.uzh.ch/davis_data.html)). | Use `shapes_rotation` for development and lock `poster_rotation` plus `boxes_rotation` as sequence-level holdouts. This split is a protocol anti-selection decision, not a result asserted by the papers. |

## 3. Why one common reference is mandatory

For event `e_k=(x_k,t_k,p_k)` and arm `a`, let
`u'_(a,k)=W_a(x_k,t_k;t_ref)` be its coordinate in the reference camera. The
focus image is formed from all valid `u'_(a,k)`. The CVPR 2018 contrast
framework measures alignment after events have been transferred to such a
reference view, not by comparing each arm in a different view.

Metric V3 consequently freezes, before any scored run:

- the half-open event window and its ordered event-ID list;
- one `t_ref` inside that window, expressed in the same captured timestamp
  domain as the events and poses;
- camera calibration and raw/rectified output-domain convention;
- interpolation, quaternion, transform direction, bilinear kernel, pixel
  center, continuous FOV, and boundary rules;
- the six arm semantics and every control parameter.

The common reference prevents an arm from gaining focus merely by choosing a
view with less displacement, a favorable crop, or a different pose sample. A
midpoint reference may reduce displacement while a boundary reference may
match an existing artifact; either can be legitimate in the literature. V3
does **not** choose between them after inspecting results. The exact choice is
a raw-byte field in the preregistration, shared by all arms.

For UZH pose notation, the dataset paper uses a camera-to-world pose. With
`R_WC(t)` denoting the event-camera orientation in world coordinates, the
rotation-only ray transfer is

```text
r_Cref = R_WC(t_ref)^T R_WC(t_k) r_Ck .
```

The source quaternion order is `(qx,qy,qz,qw)` and the paper states the JPL
convention. The protocol uses normalized, shortest-arc interpolation between
the two source pose samples bracketing `t_k`; this interpolation rule is an
engineering choice and must be byte-frozen. Quaternion sign changes must not
change the result. Reversing the relative rotation is reserved for the wrong
control, not tolerated as an alternative convention.

The calibration path is also common: raw distorted pixel, inverse UZH
pinhole-radtan calibration, calibrated ray, relative rotation, projection,
and—if the scored raster is the raw DAVIS grid—the same forward-radtan model.
The dataset paper documents the pinhole plus radial-tangential model and also
documents that DAVIS and mocap were not hardware synchronized and required
sequence acquisition precautions. Therefore any time offset/correction is a
pinned input, never a post-hoc focus optimization
([Mueggler et al., IJRR 2017](https://arxiv.org/pdf/1610.08336.pdf)).

## 4. Metric family and separation rules

### 4.1 Immutable denominator and arm contract

The arm set remains:

```text
RAW  SENSOR_FIXED  MC_CORRECT  MC_WRONG  MC_DELAYED  RETIRE_WARP
```

All arms consume the same ordered source-event IDs. Input disappearance,
duplicate ID, polarity mutation, reordered per-source identity, or synthetic
retire time is a hard protocol error. OOF is a geometry disposition, not an
event deletion. `RETIRE_WARP` exists only when every ID is bound to an
independently inspected observed retire timestamp; otherwise the six-arm assay
is HOLD rather than a five-arm PASS.

### 4.2 Primary focus metric

For arm `a`, bilinearly splat each in-FOV projected event onto a fixed
`W x H` IWE:

```text
I_a(u) = sum_k b(u - u'_(a,k))
F_a    = (1 / (W H)) sum_u (I_a(u) - mean(I_a))^2
```

`F_a` is IWE variance and higher is better. Polarity is retained in the event
record and timestamp diagnostic, but primary focus uses the single unsigned
IWE chosen before holdout. The fixed event denominator, full fixed raster, and
identical kernel are required. Any optional Gaussian width, border support, or
normalization must be selected only on `shapes_rotation`, frozen, and reported;
it cannot vary by arm or holdout sequence.

The primary effect is predeclared as a comparison of `MC_CORRECT` with
`SENSOR_FIXED`. `RAW=SENSOR_FIXED` focus equality is an expected identity when
both retain raw locality. A positive effect alone is insufficient: the
informative wrong and delayed controls must be worse than `MC_CORRECT`, and an
informative retire control must obey its predeclared logic. Confidence
interval unit, block construction, effect scale, and numerical threshold are
frozen on development data before holdout bytes are opened.

IWE focus is an accepted event-alignment objective, not independent scene
truth. Metric V3 therefore says “improved IWE focus under supplied pose,” not
“reconstructed the world correctly.”

### 4.3 Timestamp consistency is separate

Source occurrence timestamp is immutable for all arms. A delayed or retire
arm changes only the pose lookup time required by that control; it does not
rewrite the event's physical timestamp. For the timestamp diagnostic, create
polarity-separated, bilinearly accumulated average-timestamp images and report
the predeclared forward/backward loss following Zhu et al. The diagnostic is
kept separate because it measures temporal mixing within occupied pixels,
whereas IWE variance measures spatial concentration. Adding them with a tuned
weight would let one property hide failure of the other and would add a
post-hoc degree of freedom.

Timestamp loss does not become a PASS substitute if focus fails. Conversely,
a focus PASS with a timestamp-control inversion is HOLD pending root-cause
analysis.

### 4.4 Coverage and disposition are separate

For every arm and window, report at least:

```text
input_count
in_fov_count
outside_count
behind_count
invalid_count
coverage = in_fov_count / input_count
```

Coverage is neither multiplied into nor added to the focus score. A scalar
penalty can hide whether a score changed because edges aligned or because
events left the field of view. Conversely, evaluating only the arm-specific
in-FOV intersection can reward an arm for discarding difficult events. V3
therefore preserves the full denominator, reports dispositions, and applies a
separately frozen coverage non-inferiority gate. Clamp-to-border, silent drop,
arm-dependent crop, and denominator reduction are forbidden.

This separation is a V3 protocol safeguard. It is not presented as a theorem
from the focus-loss papers.

## 5. Development and sealed holdout protocol

### 5.1 Development sequence

`shapes_rotation` is development-only because it has already been used for
geometry, six-arm, nearest-anchor, and Phase-4 work. It may be used to:

- debug parsing, pose interpolation, projection, and analytic identities;
- choose window duration/event-count schedule, common-reference rule, IWE
  kernel, focus effect definition, coverage margin, bootstrap/block rule, and
  control informativeness threshold;
- verify that malformed provenance, missing pose brackets, OOF clamp/drop,
  missing/duplicate IDs, and unobserved retire times fail closed.

No development result is a dataset-generalization result. All final numerical
choices and implementation/spec source-code hashes must be committed before
holdout acquisition or inspection.

### 5.2 Holdout sequences

`poster_rotation` and `boxes_rotation` are locked, sequence-level holdouts.
Their distinct official scene descriptions—wall poster and highly textured
environment—make them useful checks against tuning only to simple shapes, but
they remain two sequences from one sensor family and do not establish broad
real-world generalization.

Holdout rules:

1. Do not inspect event plots, select visually favorable intervals, or tune a
   parameter from holdout focus/control results.
2. Freeze a deterministic window-selection rule before opening either archive.
   Eligibility may use only source integrity, timestamp/pose bracketing, and
   predeclared minimum-count rules—not arm scores. All eligible windows, or a
   hash-seeded sample fixed from captured archive identity, must be retained.
3. Execute both holdout sequences once with the same code/spec. Record all
   windows, including expected rejects and errors.
4. A strict holdout GO requires the frozen primary direction/threshold and
   coverage/control gates on **both** sequences plus the predeclared pooled
   analysis. One sequence cannot compensate for failure of the other.
5. After first unblinding, any code/spec change creates a new version and new
   untouched holdout requirement; it cannot overwrite V3.

No holdout bytes or results were downloaded or inspected while authoring this
document.

## 6. Official acquisition URLs and license authority

The authority page exposes relative links under
`https://rpg.ifi.uzh.ch/`. The following are their resolved official URLs as of
2026-08-21. Metric V3 selects the **Text ZIP** for its text importer; rosbag and
plot URLs are recorded for identity and must not be substituted silently.

| Sequence | Official Text ZIP — acquisition input | Official rosbag | Official plots |
|---|---|---|---|
| `poster_rotation` | [poster_rotation.zip](https://rpg.ifi.uzh.ch/datasets/davis/poster_rotation.zip) | [poster_rotation.bag](https://rpg.ifi.uzh.ch/datasets/davis/poster_rotation.bag) | [poster_rotation_plots.zip](https://rpg.ifi.uzh.ch/datasets/davis/poster_rotation_plots.zip) |
| `boxes_rotation` | [boxes_rotation.zip](https://rpg.ifi.uzh.ch/datasets/davis/boxes_rotation.zip) | [boxes_rotation.bag](https://rpg.ifi.uzh.ch/datasets/davis/boxes_rotation.bag) | [boxes_rotation_plots.zip](https://rpg.ifi.uzh.ch/datasets/davis/boxes_rotation_plots.zip) |

The official dataset page states that the datasets are released under
[CC BY-NC-SA 3.0](http://creativecommons.org/licenses/by-nc-sa/3.0/) and calls
out non-commercial use including research. The governing dataset statement is
the [UZH authority page](https://rpg.ifi.uzh.ch/davis_data.html); the linked
Creative Commons page supplies the license text. This protocol records that
published statement and does not make a legal conclusion or expand its scope.
Use of the dataset must cite the official dataset paper
([Mueggler et al., IJRR 2017](https://doi.org/10.1177/0278364917691115)).

### 6.1 Capture and provenance procedure

Large archives are external captured-byte inputs and must not be committed to
Git. When acquisition is authorized, perform these steps once per sequence:

1. Fetch the exact Text-ZIP URL without using a search result or mirror. Record
   acquisition UTC time, requested URL, redirect chain/final URL, HTTP status,
   byte length, and available `ETag`/`Last-Modified`. Headers are metadata, not
   content identity.
2. Before extraction, compute SHA-256 over the exact received archive bytes and
   record file size. Evaluation always reopens those captured bytes; it does
   not transparently redownload a mutable URL.
3. Inventory the ZIP deterministically before extraction. Reject duplicate
   member names, absolute paths, `..` traversal, NUL names, links/special files,
   encrypted members, unsupported compression, or declared/uncompressed sizes
   above predeclared resource bounds.
4. Record every member's exact archive path, uncompressed byte count, CRC, and
   recomputed SHA-256. Bind the exact required `events.txt`,
   `groundtruth.txt`, and `calib.txt` member identities. Do not infer a member
   from basename when two candidates exist.
5. Capture and hash the UZH authority-page HTML and linked license-page bytes
   separately with access time. These snapshots document what was observed;
   they do not cryptographically authenticate UZH or replace the live
   authority.
6. Emit a small canonical receipt containing dataset/sequence name, all URLs,
   archive/member hashes, importer/spec/implementation commit hashes, license
   authority URLs, split=`holdout`, and `generated_artifact_official_uzh=false`.
   Publish data, receipt, and `COMPLETE` atomically with `COMPLETE` last.
7. Keep raw archives and extracted large members outside the repository. Commit
   only protocol/spec/code and small receipts or aggregates permitted by the
   project policy. Do not redistribute raw or derived bytes without a separate
   license/compliance check.

Any source URL substitution, changed captured hash, ambiguous member, partial
publication, receipt mismatch, or unknown license capture is HOLD/fail-closed;
it is never repaired by rehashing a substituted source and calling it the same
holdout.

## 7. Rotation-only claim boundary

### Allowed only after all frozen gates pass

> On the preregistered windows of the official UZH `poster_rotation` and
> `boxes_rotation` captured-byte inputs, using supplied event-time orientation
> and official calibration, MC-WTB `MC_CORRECT` improved the frozen bilinear-IWE
> focus metric over `SENSOR_FIXED`, while meeting separately reported coverage,
> timestamp, identity, and informative-control gates.

Even that wording must include the effect, uncertainty, window/event
denominators, coverage counts, exact source/spec/code identities, and both
sequence verdicts. If a gate fails, publish FAIL/HOLD without replacing the
metric or selecting another window.

### Explicitly forbidden promotions

Metric V3 cannot by itself claim:

- metric-independent reconstruction accuracy or a correct world map;
- translation compensation, depth correctness, general 6-DoF alignment, or
  independently moving-object correction;
- removal of sensor-side/pre-capture motion artifact, global hold, or exposure
  blur;
- pose-estimation accuracy—the protocol consumes supplied motion;
- benefit on other UZH sequences, sensors, datasets, window distributions, or
  arbitrary real scenes;
- codec compression, bandwidth reduction, losslessness, endpoint throughput,
  RTL correctness, 45 nm timing/area/power, or system PPA benefit;
- that a generated receipt/artifact is an official UZH artifact, or that
  SHA-256 proves publisher authenticity or legal compliance.

The official word “rotation” is an acquisition-category label, not a guarantee
that every event is generated by an exact pure rotation or rigid static scene.
Because V3 does not use translation or depth, the honest scientific noun is
**orientation-only event-coordinate compensation on UZH rotation-labelled
sequences**. Translation, parallax, residual clock/calibration error, dynamic
objects, and OOF remain possible explanations for a HOLD or FAIL.

## 8. Primary-source ledger

1. UZH Robotics and Perception Group, [The Event-Camera Dataset and Simulator — official dataset and license authority](https://rpg.ifi.uzh.ch/davis_data.html).
2. E. Mueggler et al., [The Event-Camera Dataset and Simulator: Event-based Data for Pose Estimation, Visual Odometry, and SLAM, IJRR 2017](https://arxiv.org/pdf/1610.08336.pdf), [DOI](https://doi.org/10.1177/0278364917691115).
3. E. Mueggler, G. Gallego, D. Scaramuzza, [Continuous-Time Trajectory Estimation for Event-based Vision Sensors, RSS 2015](https://rpg.ifi.uzh.ch/docs/RSS15_Mueggler.pdf).
4. G. Gallego, D. Scaramuzza, [Accurate Angular Velocity Estimation with an Event Camera, IEEE RA-L 2017](https://rpg.ifi.uzh.ch/docs/RAL16_Gallego.pdf), [DOI](https://doi.org/10.1109/LRA.2016.2647639).
5. G. Gallego, H. Rebecq, D. Scaramuzza, [A Unifying Contrast Maximization Framework for Event Cameras, CVPR 2018](https://rpg.ifi.uzh.ch/docs/CVPR18_Gallego.pdf), [CVF record](https://openaccess.thecvf.com/content_cvpr_2018/html/Gallego_A_Unifying_Contrast_CVPR_2018_paper.html).
6. G. Gallego, M. Gehrig, D. Scaramuzza, [Focus Is All You Need: Loss Functions for Event-Based Vision, CVPR 2019](https://openaccess.thecvf.com/content_CVPR_2019/papers/Gallego_Focus_Is_All_You_Need_Loss_Functions_for_Event-Based_Vision_CVPR_2019_paper.pdf).
7. A. Zhu et al., [Unsupervised Event-based Optical Flow using Motion Compensation, ECCV Workshops 2018](https://openaccess.thecvf.com/content_ECCVW_2018/papers/11134/Zhu_Unsupervised_Event-based_Optical_Flow_using_Motion_Compensation_ECCVW_2018_paper.pdf).
