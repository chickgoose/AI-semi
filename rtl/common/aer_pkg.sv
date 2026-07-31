package aer_pkg;
  parameter int unsigned DEFAULT_NUM_SOURCES = 8;

  function automatic int unsigned index_width(input int unsigned item_count);
    if (item_count <= 1) begin
      return 1;
    end
    return $clog2(item_count);
  endfunction
endpackage
