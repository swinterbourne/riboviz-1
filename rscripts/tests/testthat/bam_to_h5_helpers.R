#' Helper functions for `bam_to_h5`-related tests.
#'
#' @export

suppressMessages(library(here, quietly = T))
suppressMessages(library(testthat, quietly = T))
suppressMessages(library(Biostrings, quietly = T))
suppressMessages(library(GenomicAlignments, quietly = T))
suppressMessages(library(Rsamtools, quietly = T))

source(here::here("rscripts", "read_count_functions.R"))

#' Validate H5 data for a specific sequence and feature.
#'
#' @param sequence Sequence name (character).
#' @param feature_name Feature name (character).
#' @param h5_file H5 file with data to be validated (character).
#' @param gff GFF data (tbl_df tbl data.frame).
#' @param bam_hdr_seq_info Data on sequences from BAM file header
#' (GenomeInfoDb::Seqinfo).
#' @param bam Data on alignments from BAM file
#' (GenomicAlignments::GAlignments).
#' @param dataset Human-readable name of the dataset (character).
#' @param min_read_length Minimum read length in H5 output (integer).
#' @param max_read_length Maximum read length in H5 output (integer).
#' @param buffer Length of flanking region around the feature (integer).
#' @param is_riboviz_gff Does the GFF file contain 3 elements per gene
#' - UTR5, feature, and UTR3? (logical).
#' @param stop_in_feature Are stop codons part of the feature
#' annotations in GFF? (logical).
#' @param feature Feature e.g. `CDS`, `ORF`, or `uORF` (character).
#'
#' @export
ValidateH5SequenceFeature <- function(sequence, feature_name,
  h5_file, gff, bam_hdr_seq_info, bam, dataset, min_read_length,
  max_read_length, buffer, is_riboviz_gff, stop_in_feature,
  feature = "CDS") {
  num_read_counts <- max_read_length - min_read_length + 1
  # Get positions from GFF
  gff_cds_start <- GetCDS5start(feature_name, gff, ftype = feature)
  gff_cds_end <- GetCDS3end(feature_name, gff, ftype = feature)
  gff_cds_length <- gff_cds_end - gff_cds_start + 1
  stop_codon_offset <- 2
  if (is_riboviz_gff) {
    # Get positions from GFF
    utr5_start <- GetCDS5start(feature_name, gff, ftype = "UTR5")
    utr5_end <- GetCDS3end(feature_name, gff, ftype = "UTR5")
    utr5_length <- utr5_end - utr5_start + 1
    utr3_start <- GetCDS5start(feature_name, gff, ftype = "UTR3")
    utr3_end <- GetCDS3end(feature_name, gff, ftype = "UTR3")
    utr3_length <- utr3_end - utr3_start + 1
    h5_buffer_left_info <-
      "Unexpected buffer_left, compared to GFF UTR5 length"
    h5_buffer_right_info <-
      "Unexpected buffer_right, compared to GFF UTR5 length"
    h5_stop_codon_info <-
      "Unexpected stop_codon_pos, compared to GFF feature positions"
  } else {
    # Get positions from GFF and buffer
    utr5_start <- 1
    utr5_length <- buffer
    utr5_end <- utr5_start + buffer - 1
    utr3_start <- gff_cds_end + 1
    utr3_length <- buffer
    utr3_end <- utr3_start + buffer - 1
    if (! stop_in_feature) {
      stop_codon_offset <- -1
    }
    h5_buffer_left_info <-
      "Unexpected buffer_left, compared to 'buffer' parameter"
    h5_buffer_right_info <-
      "Unexpected buffer_right, compared to 'buffer' parameter"
    h5_stop_codon_info <-
      "Unexpected stop_codon_pos, compared to that derived from 'buffer' parameter"
  }
  stop_codon_loc <- gff_cds_end - stop_codon_offset
  stop_codon_pos <- as.array(seq(stop_codon_loc, stop_codon_loc + 2))

  print(paste0("UTR5 start/length/end: ", utr5_start, " ",
    utr5_length, " ", utr5_end))
  print(paste0(feature, " start/length/end: ", gff_cds_start, " ",
    gff_cds_length, " ", gff_cds_end))
  print(paste0("UTR3 start/length/end: ", utr3_start, " ",
    utr3_length, " ", utr3_end))

  # Get sequence length from BAM header
  bam_hdr_sequence <- bam_hdr_seq_info[sequence] # GenomeInfoDb::Seqinfo, S4
  bam_hdr_sequence_seq_length <- bam_hdr_sequence@seqlengths
  print(paste0("Sequence length: ", bam_hdr_sequence_seq_length))

  # Get sequence entries from BAM
  # GenomicAlignments::GAlignments, S4
  bam_sequence <- bam[(GenomicAlignments::seqnames(bam) == sequence)]
  print(paste0("Number of alignments: ", length(bam_sequence)))
  bam_sequence_kept <- bam_sequence[
    (mcols(bam_sequence)$flag %in% c(0, 256))]
  print(paste0("Number of alignments (Flag = 0|256): ",
    length(bam_sequence_kept)))
  bam_sequence_discard <- bam_sequence[
    (!(mcols(bam_sequence)$flag %in% c(0, 256)))]
  print(paste0("Number of alignments (Flag != 0|256): ",
    length(bam_sequence_discard)))

  # Validate buffer_left: number of nucleotides upstream of the start
  # codon (ATG) (UTR5 length)
  h5_buffer_left <- GetGeneBufferLeft(feature_name, dataset,
                                      h5_file) # double
  print(paste0("buffer_left: ", h5_buffer_left))
  testthat::expect_equal(h5_buffer_left, utr5_length,
    info = paste0(feature_name, ": ", h5_buffer_left_info))

  # Validate buffer_right: number of nucleotides downstream of the
  # stop codon (TAA/TAG/TGA) (UTR3 length)
  h5_buffer_right <- GetGeneBufferRight(feature_name, dataset,
                                        h5_file) # integer
  print(paste0("buffer_right: ", h5_buffer_right))
  buffer_right <- utr3_end - stop_codon_pos[3]
  testthat::expect_equal(h5_buffer_right, buffer_right,
    info = paste0(feature_name, ": ", h5_buffer_right_info))

  # Validate start_codon_pos: Positions corresponding to start codon
  # of feature in organism sequence
  gff_start_codon_pos <- as.array(seq(gff_cds_start, gff_cds_start + 2))
  h5_start_codon_pos <-
    GetGeneStartCodonPos(feature_name, dataset, h5_file) # 1D array of 3 integer
  print(paste0("start_codon_pos: ", toString(h5_start_codon_pos)))
  testthat::expect_equal(length(h5_start_codon_pos), 3,
    info = paste0(feature_name, ": Unexpected start_codon_pos length"))
  testthat::expect_equal(h5_start_codon_pos, gff_start_codon_pos,
    info = paste0(feature_name,
      ": Unexpected start_codon_pos, compared to GFF feature positions"))

  # Validate stop_codon_pos: Positions corresponding to stop codon of
  # feature in organism sequence
  h5_stop_codon_pos <-
    GetGeneStopCodonPos(feature_name, dataset, h5_file) # 1D array of 3 integer
  print(paste0("stop_codon_pos: ", toString(h5_stop_codon_pos)))
  testthat::expect_equal(length(h5_stop_codon_pos), 3,
    info = paste0(feature_name, ": Unexpected stop_codon_pos length"))
  testthat::expect_equal(h5_stop_codon_pos, stop_codon_pos,
    info = paste0(feature_name, ": ", h5_stop_codon_info))

  # Validate lengths: Lengths of mapped reads.
  lengths <- as.array(seq(min_read_length, max_read_length))
  # 1D array of <max_read_length - min_read_length + 1> integer
  h5_lengths <- GetGeneMappedReadLengths(feature_name, dataset, h5_file)
  print(paste0("lengths: ", toString(h5_lengths)))
  testthat::expect_equal(length(h5_lengths), num_read_counts,
    info = paste0(feature_name,
      ": lengths length != max_read_length - min_read_length + 1"))
  testthat::expect_equal(h5_lengths, lengths,
    info = paste0(feature_name, ": Unexpected lengths"))

  # Validate reads_by_len: Counts of number of ribosome sequences of
  # each length
  # 1D array of <max_read_length - min_read_length + 1> double
  h5_reads_by_len <- GetGeneReadLength(feature_name, dataset, h5_file)
  print(paste0("reads_by_len: ", toString(h5_reads_by_len)))
  testthat::expect_equal(length(h5_reads_by_len), num_read_counts,
    info = paste0(feature_name,
      ": reads_by_len length != max_read_length - min_read_length + 1"))

  # Calculate expected reads_by_len based on information from BAM
  reads_by_len_bam <- as.array(integer(num_read_counts))
  for (width in sort(GenomicAlignments::qwidth(bam_sequence_kept))) {
    index <- width - min_read_length + 1
    reads_by_len_bam[index] <- reads_by_len_bam[index] + 1
  }
  print(paste0("reads_by_len_bam: ", toString(reads_by_len_bam)))
  testthat::expect_equal(h5_reads_by_len, reads_by_len_bam,
    info = paste0(feature_name,
      ": Unexpected reads_by_len, compared to those computed from BAM"))

  # Validate reads_total: Total number of ribosome sequences
  h5_reads_total <-
    GetGeneReadsTotal(feature_name, dataset, h5_file) # 1D array of 1 double
  print(paste0("reads_total: ", h5_reads_total))
  testthat::expect_equal(length(h5_reads_total), 1,
    info = paste0(feature_name, ": Unexpected reads_total length"))
  h5_reads_len_total <- Reduce("+", h5_reads_total)
  testthat::expect_equal(h5_reads_total[1], h5_reads_len_total,
    info = paste0(feature_name,
      ": reads_total != sum of totals in reads_by_len"))
  testthat::expect_equal(h5_reads_total[1], length(bam_sequence_kept),
    info = paste0(feature_name,
      ": reads_total != number of BAM alignments with Flag = 0"))

  # Validate data: Positions and lengths of ribosome sequences within
  # the organism data
  h5_data <- GetGeneDatamatrix(feature_name, dataset, h5_file) # matrix, integer
  print(paste0("data rows/columns: ", toString(dim(h5_data))))
  testthat::expect_equal(nrow(h5_data), num_read_counts,
    info = paste0(feature_name,
      ": Number of data rows != max_read_length - min_read_length + 1"))
  testthat::expect_equal(ncol(h5_data), utr3_end,
    info = paste0(feature_name,
      ": Number of data columns != GFF UTR3 final nt position"))
  testthat::expect_equal(ncol(h5_data), bam_hdr_sequence_seq_length,
    info = paste0(feature_name,
      ": Number of data columns != BAM sequence length"))
  h5_reads_by_len_data <- rowSums(h5_data)
  testthat::expect_equal(h5_reads_by_len, as.array(h5_reads_by_len_data),
    info = paste0(feature_name, ": reads_by_len is not consistent with data"))

  # Calculate expected data based on information from BAM
  data <- matrix(0, nrow = num_read_counts, ncol = utr3_end)
  if (sequence %in% GenomicAlignments::seqnames(bam)) {
    print("Sequence has alignments in BAM.")
    for (align in names(bam_sequence_kept)) {
      start <- GenomicAlignments::start(bam_sequence_kept[align])
      width <- GenomicAlignments::qwidth(bam_sequence_kept[align])
      width <- width - min_read_length + 1
      data[width, start] <- data[width, start] + 1
    }
    testthat::expect_equal(h5_data, data,
      info = paste0(feature_name,
        ": Unexpected data, compared to that computed from BAM"))
  } else {
      print("Sequence has no alignments in BAM.")
      testthat::expect_equal(h5_data, data,
        info = paste0(feature_name,
          ": Unexpected data, expected 0s as no alignments in BAM"))
  }
  reads_by_len_data <- rowSums(data)
  testthat::expect_equal(h5_reads_by_len_data, reads_by_len_data,
    info = paste0(feature_name,
      ": Unexpected reads_by_len length, compared to those computed from BAM"))
  testthat::expect_equal(h5_reads_by_len, as.array(reads_by_len_data),
    info = paste0(feature_name,
      ": Unexpected reads_by_len, compared to those computed from BAM"))
}

#' Validate H5 link for secondary feature names. This function checks
#' that a link for `secondary_name` exists, is of type
#' `H5L_TYPE_EXTERNAL` and the data accessible via that link equals
#' that of `primary_name` in the same file.
#'
#' @param h5_file H5 file with data to be validated
#' (character).
#' @param primary_name Primary feature name to access the data
#' (character).
#' @param secondary_name Secondary feature name to access the data
#' (character).
#'
#' @export
ValidateSecondaryLink <- function(h5_file, primary_name, secondary_name) {
  link <- paste0("/", secondary_name)
  # Check link.
  fid <- rhdf5::H5Fopen(h5_file)
  link_exists <- rhdf5::H5Lexists(fid, link)
  rhdf5::H5Fclose(fid)
  testthat::expect_true(link_exists, info = "Missing link")
  fid <- rhdf5::H5Fopen(h5_file)
  info <- rhdf5::H5Lget_info(fid, link)
  rhdf5::H5Fclose(fid)
  testthat::expect_equal(info$type, "H5L_TYPE_EXTERNAL",
    info = "Expected link type to be H5L_TYPE_EXTERNAL")
  # Check data accessible via secondary_name is equal to that
  # accessible via primary_name.
  data_p <- rhdf5::h5read(file = h5_file,
                          name = paste0("/", primary_name))
  data_s <- rhdf5::h5read(file = h5_file,
                          name = paste0("/", secondary_name))
  testthat::expect_true(identical(data_p, data_s),
    info = "Primary data does not equal secondary data")
}

#' Validate H5 data within a riboviz H5 data file.
#'
#' @param h5_file H5 file with data to be validated
#' (character).
#' @param gff_file GFF file (character).
#' @param bam_file BAM file (character).
#' @param primary_id Primary gene IDs to access the data (character).
#' @param secondary_id Secondary gene IDs to access the data (character).
#' @param dataset Human-readable name of the dataset (character).
#' @param min_read_length Minimum read length in H5 output (integer).
#' @param max_read_length Maximum read length in H5 output (integer).
#' @param buffer Length of flanking region around the feature (integer).
#' @param is_riboviz_gff Does the GFF file contain 3 elements per gene
#' - UTR5, feature, and UTR3? (logical).
#' @param stop_in_feature Are stop codons part of the feature
#' annotations in GFF? (logical).
#' @param feature Feature e.g. `CDS`, `ORF`, or `uORF` (character).
#'
#' @export
ValidateH5 <- function(h5_file, gff_file, bam_file, primary_id,
  secondary_id, dataset, min_read_length, max_read_length, buffer,
  is_riboviz_gff, stop_in_feature, feature = "CDS") {

  gff <- readGFFAsDf(gff_file) # tbl_df tbl data.frame, list
  gff_names <- unique(gff$seqnames) # factor, integer
  print(paste0("GFF sequence names (", length(gff_names), "):"))
  print(gff_names)
  gff_primary_ids <- unique(gff[[primary_id]])
  print(paste0("GFF primary feature IDs (", length(gff_primary_ids), "):"))
  print(gff_primary_ids)
  gff_secondary_ids <- c()
  if (!is.na(secondary_id)) {
    gff_secondary_ids <- unique(gff[[secondary_id]])
    print(paste0("GFF secondary feature IDs (",
                 length(gff_secondary_ids), "):"))
    print(gff_secondary_ids)
  }

  bam_file_f <- Rsamtools::BamFile(bam_file)
  bam_hdr_seq_info <- Rsamtools::seqinfo(bam_file_f) # GenomeInfoDb::Seqinfo, S4
  bam_hdr_names <- bam_hdr_seq_info@seqnames # character, character
  print(paste0("BAM header sequence names (", length(bam_hdr_names), "):"))
  print(bam_hdr_names)

  # By default readGAlignments returns: seqnames, strand, cigar,
  # qwidth, start, end, width. Also want "flag" so specify
  # explicitly.
  bam_params <- Rsamtools::ScanBamParam(what = c("flag"))
  bam <- GenomicAlignments::readGAlignments(bam_file,
    param = bam_params, use.names = T) # GenomicAlignments::GAlignments, S4
  print(paste0("Number of BAM alignments: ", length(bam)))
  bam_names <- unique(sort(GenomicAlignments::seqnames(bam))) # factor, integer
  print(paste0("BAM sequence names (", length(bam_names), "):"))
  print(bam_names)

  h5_data <- rhdf5::h5ls(h5_file, recursive = 1) # data.frame, list
  h5_names <- h5_data$name # character, character
  print(paste0("H5 sequence names (", length(h5_names), "):"))
  print(h5_names)

  ## VALIDATE H5 (sequence-specific)

  for (feature_name in gff_primary_ids) {
    print(paste0("Feature name (", primary_id, "): ", feature_name))
    # Get sequence corresponding to feature.
    gff_feature <- gff %>%
      filter(.[[primary_id]] == feature_name) %>%
      filter(type == feature)
    sequence <- (as.character(gff_feature$seqnames))[1] # Expect only one.
    print(paste0("Sequence: ", sequence))
    testthat::expect_true(sequence %in% bam_hdr_names,
        info = "Sequence name should be BAM header")
    testthat::expect_true(sequence %in% bam_names,
        info = "Sequence name should be BAM body")
    ValidateH5SequenceFeature(sequence, feature_name, h5_file, gff,
      bam_hdr_seq_info, bam, dataset, min_read_length,
      max_read_length, buffer, is_riboviz_gff, stop_in_feature, feature)
    if (!is.na(secondary_id)) {
      secondary_name <- (as.character(gff_feature[[secondary_id]]))[1]
      print(paste0("Feature name (secondary) (",
                   secondary_id, "): ", secondary_name))
      ValidateSecondaryLink(h5_file, feature_name, secondary_name)
    }
  }
}
