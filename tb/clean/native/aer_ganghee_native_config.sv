// Xcelium configuration: preserve aer_clean_tb and replace only its candidate
// calibration cell with the TB-only Ganghee native protocol binding.
config aer_ganghee_native_config;
  design work.aer_clean_tb;
  instance aer_clean_tb.candidate
    use work.aer_ganghee_native_binding;
endconfig
