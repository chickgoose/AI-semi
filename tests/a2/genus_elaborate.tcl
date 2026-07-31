set project_root [file normalize [file join [file dirname [info script]] ../..]]
set liberty_file "/home/aiasic26911/gsclib045_all_v4.7/gsclib045/timing/slow_vdd1v0_basicCells.lib"

if {![file exists $liberty_file]} {
    error "Required GPDK045 Liberty not found: $liberty_file"
}

set_db init_lib_search_path [file dirname $liberty_file]
set_db library [list $liberty_file]

set rtl_files [list \
    [file join $project_root rtl/improved/aer_sync_fifo.sv] \
    [file join $project_root rtl/improved/aer_round_robin_arbiter.sv] \
    [file join $project_root rtl/improved/aer_event_buffer.sv] \
    [file join $project_root rtl/improved/aer_dut.sv]]

read_hdl -sv $rtl_files
elaborate aer_dut -parameters [list 4 16 4]

check_design -unresolved

set occupancy_ports [get_db ports -if {.name =~ *occupancy*}]
set occupancy_nets_elab [get_db nets -if {.name =~ *occupancy*}]
puts "A2_GENUS_ELAB_PASS top=aer_dut sources=4 addr_width=16 fifo_depth=4"
puts "A2_GENUS_LIBRARY $liberty_file"
puts "A2_OCCUPANCY_PORTS [llength $occupancy_ports]"
puts "A2_OCCUPANCY_NETS_AFTER_ELAB [llength $occupancy_nets_elab]"

syn_generic

set occupancy_nets_generic [get_db nets -if {.name =~ *occupancy*}]
puts "A2_GENUS_GENERIC_PASS"
puts "A2_OCCUPANCY_NETS_AFTER_GENERIC [llength $occupancy_nets_generic]"

exit
