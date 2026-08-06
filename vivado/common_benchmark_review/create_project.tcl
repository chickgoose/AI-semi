set project_root "C:/Users/박준영/AI-semi/vivado/common_benchmark_review"
set source_root "$project_root/src"
set project_dir "$project_root/project"

file mkdir $project_dir

set selected_part ""
foreach candidate {xc7a35tcpg236-1 xc7a100tcsg324-1 xc7z020clg400-1} {
  if {[llength [get_parts -quiet $candidate]] > 0} {
    set selected_part $candidate
    break
  }
}
if {$selected_part eq ""} {
  set installed_parts [get_parts -quiet]
  if {[llength $installed_parts] == 0} {
    error "No FPGA device parts are installed in Vivado"
  }
  set selected_part [lindex $installed_parts 0]
}

create_project aer_common_tb_review $project_dir -part $selected_part -force
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]

set simulation_files [list \
  "$source_root/tb/clean/aer_bench_if.sv" \
  "$source_root/tb/clean/aer_clean_mock_candidate.sv" \
  "$source_root/tb/clean/aer_legacy_candidate_adapter.sv" \
  "$source_root/tb/clean/aer_clean_assertions.sv" \
  "$source_root/tb/clean/aer_clean_tb.sv" \
  "$source_root/tb/clean/native/aer_ganghee_native_binding.sv" \
  "$source_root/tests/clean_native/ganghee_native_protocol_mock.sv" \
  "$source_root/tests/clean_native/aer_ganghee_native_binding_tb.sv"]

foreach source_file $simulation_files {
  if {![file exists $source_file]} {
    error "Missing copied SystemVerilog source: $source_file"
  }
}

add_files -norecurse -fileset sim_1 $simulation_files
set_property file_type SystemVerilog [get_files -of_objects [get_filesets sim_1]]
set_property top aer_clean_tb [get_filesets sim_1]
set_property xsim.simulate.runtime 3us [get_filesets sim_1]
update_compile_order -fileset sim_1

puts "AER_COMMON_TB_PROJECT=$project_dir/aer_common_tb_review.xpr"
puts "AER_COMMON_TB_TOP=aer_clean_tb"
puts "AER_COMMON_TB_PART=$selected_part"
close_project
