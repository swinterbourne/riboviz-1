"""
Scan input, temporary and output directories and count the number of
reads (sequences) processed by specific stages of a workflow. The scan
is based on the configuration, directory structure and file patterns
used by the workflow.

The following information is included:

* Input files: number of reads in the FASTQ files used as inputs.
* ``cutadapt``: number of reads in the FASTQ file output.
* :py:mod:`riboviz.tools.demultiplex_fastq`:  number of reads in the
  FASTQ files output, using the information in the associated
  ``num_reads.tsv`` summary files, or, if these can't be found, the
  FASTQ files themselves.
* ``hisat2``: number of reads in the SAM file and FASTQ file output.
* :py:mod:`riboviz.tools.trim_5p_mismatch`: number of reads in the SAM
  file output as recorded in the ``trim_5p_mismatch.tsv`` summary file
  output, or the SAM file itself, if the TSV file cannot be found.
* ``umi_tools dedup``: number of reads in the BAM file output.

The output file is a TSV file with columns:

* ``SampleName``: Name of the sample to which this file belongs. This
  is an empty value if the step was not sample-specific
  (e.g. processing a a multiplexed FASTQ file).
* ``Program``: Program that wrote the file. The special token
  ``input`` denotes input files.
* ``File``: Path to file.
* ``NumReads``: Number of reads in the file.
* ``Description``: Human-readable description of the file contents.

For example::

    # Created by: riboviz
    # Date: 2020-02-12 06:34:50.340316
    # Command-line tool: /home/ubuntu/riboviz/riboviz/tools/count_reads.py
    # File: /home/ubuntu/riboviz/riboviz/count_reads.py
    # Version: commit 0c39652154fd9623f2cebbace6e741f54d712b32 date 2020-02-12 06:24:19-08:00
    SampleName	Program	File	NumReads	Description
    WTnone	input	vignette/input/SRR1042855_s1mi.fastq.gz	963571	input
    WT3AT	input	vignette/input/SRR1042864_s1mi.fastq.gz	1374448	input
    WT3AT	cutadapt	vignette/tmp/WT3AT/trim.fq	1373362	Reads after removal of sequencing library adapters
    WT3AT	hisat2	vignette/tmp/WT3AT/nonrRNA.fq	486233	rRNA or other contaminating reads removed by alignment to rRNA index files
    WT3AT	hisat2  vignette/tmp/WT3AT/rRNA_map.sam	1373362	Reads with rRNA and other contaminating reads removed by alignment to rRNA index files

"""
import glob
import os
import os.path
import multiprocessing
import yaml
import pandas as pd
from riboviz import demultiplex_fastq
from riboviz import fastq
from riboviz import params
from riboviz import provenance
from riboviz import sam_bam
from riboviz import sample_sheets
from riboviz import trim_5p_mismatch
from riboviz import workflow_files
from riboviz.tools import demultiplex_fastq as demultiplex_fastq_tools_module
from riboviz.tools import trim_5p_mismatch as trim_5p_mismatch_tools_module

SAMPLE_NAME = "SampleName"
""" Column name. """
PROGRAM = "Program"
""" Column name. """
FILE = "File"
""" Column name. """
NUM_READS = "NumReads"
""" Column name. """
DESCRIPTION = "Description"
""" Column name. """
HEADER = [SAMPLE_NAME, PROGRAM, FILE, NUM_READS, DESCRIPTION]
""" File header. """
INPUT = "input"
""" ``Program`` value to denote input files """


def input_fq(config_file, input_dir, pool=None):
    """
    Extract names of FASTQ input files from workflow configuration
    file and count the number of reads in each file.

    The configuration file is checked to see if it has an ``fq_files``
    key whose value is mappings from sample names to sample files
    (relative to ``input_dir``). Each FASTQ file has its reads
    counted.

    If there is no ``fq_files`` key but there is a
    ``multiplex_fq_files`` key then the value of this key is assumed
    to be a list of multiplexed input files (relative to
    ``input_dir``). Each FASTQ file has its reads counted.

    If both keys exist then both sets of input files are traversed.

    If neither key exists then no input files are traversed.

    For each file a ``pandas.core.frame.Series`` is created with
    fields ``SampleName`` (sample name recorded in configuration or,
    for multiplexed files, ``''``), ``Program`` (set to ``input``),
    ``File``, ``NumReads``, ``Description`` (``input``).

    Parameter ``pool`` is used for multiprocessing:

    * If it is not specified, then the code will run in one process,
      and return a list of Pandas dataframes,
      (``pandas.core.frame.Series``).
    * If it is specified, the code will use the workers of the pool to
      run different samples in parallel, and return a list of
      ``multiprocessing.pool.ApplyResult``, where the Pandas
      dataframes can be obtained by the ``.get()`` function on these
      objects.

    :param config_file: Configuration file
    :type config_file: str or unicode
    :param input_dir: Directory
    :type input_dir: str or unicode
    :param pool: multiprocessing pool object that the process will be \
    joined into
    :type pool: multiprocessing.Pool
    :return: list of ``pandas.core.frame.Series`` or ``[]`` if \
    ``pool`` is not specified. If ``pool`` is specified, then the return \
    list will be a list of ``multiprocessing.pool.ApplyResult``.
    :rtype: list(pandas.core.frame.Series) OR\
    list(multiprocessing.pool.ApplyResult).
    """
    with open(config_file, 'r') as f:
        config = yaml.load(f, yaml.SafeLoader)
    rows = []
    if params.FQ_FILES in config and config[params.FQ_FILES] is not None:
        sample_files = [(sample_name, os.path.join(input_dir, file_name))
                        for sample_name, file_name in
                        list(config[params.FQ_FILES].items())]
    else:
        sample_files = []
    if params.MULTIPLEX_FQ_FILES in config \
       and config[params.MULTIPLEX_FQ_FILES] is not None:
        multiplex_files = [("", os.path.join(input_dir, file_name))
                           for file_name in config[params.MULTIPLEX_FQ_FILES]]
    else:
        multiplex_files = []
    files = sample_files + multiplex_files
    for (sample_name, file_name) in files:
        print(file_name)
        try:
            if pool is None:
                # Serial version.
                rows.append(_input_fq_count(sample_name, file_name))
            else:
                # Use the worker of pool to execute in parallel.
                rows.append(pool.apply_async(_input_fq_count,
                                             args=(sample_name, file_name,)))
        except Exception as e:
            print(e)
            continue
    return rows


def _input_fq_count(sample_name, file_name):
    """
    Extract names of FASTQ input files from workflow configuration
    file and count the number of reads in each file.

    ``sample_name`` is a sample name e.g. "JEC21" and ``file_name``
    is the path to file.

    :param sample_name: sample name
    :type sample_name: str or unicode
    :param file_name: path to file
    :type file_name: str or unicode
    :return: ``pandas.core.frame.Series``, or ``None``
    :rtype: pandas.core.frame.Series
    """
    try:
        num_reads = fastq.count_sequences(file_name)
        return pd.DataFrame(
            [[sample_name, INPUT, file_name, num_reads, INPUT]],
            columns=HEADER)
    except Exception as e:
        print(e)
        # Return None so that this sample/file combination will be
        # treated as invalid in later processing.
        return None


def cutadapt_fq(tmp_dir, sample=""):
    """
    Count number of reads in the FASTQ file output by ``cutadapt``.

    ``<tmp_dir>/<sample>`` is searched for a FASTQ file matching
    :py:const:`riboviz.workflow_files.ADAPTER_TRIM_FQ`. Any file
    also matching :py:const:`riboviz.workflow_files.UMI_EXTRACT_FQ`
    is then removed (these file names overlap). The number of reads in
    the resulting file are counted.

    A ``pandas.core.frame.Series`` is created with fields
    ``SampleName``, ``Program``, ``File``, ``NumReads``,
    ``Description``.

    :param tmp_dir: Directory
    :type tmp_dir: str or unicode
    :param sample: Sample name
    :type sample: str or unicode
    :return: ``pandas.core.frame.Series``, or ``None``
    :rtype: pandas.core.frame.Series
    """
    fq_files = glob.glob(os.path.join(
        tmp_dir, sample, "*" + workflow_files.ADAPTER_TRIM_FQ))
    # If using with FASTQ files then there may be a
    # file with extension "_extract_trim.fq" which also will be
    # caught by the glob above, so remove this file name.
    umi_files = glob.glob(os.path.join(
        tmp_dir, sample, "*" + workflow_files.UMI_EXTRACT_FQ))
    fq_files = [file_name for file_name in fq_files
                if file_name not in umi_files]
    if not fq_files:
        return None
    fq_file = fq_files[0]  # Only 1 match expected.
    print(fq_file)
    try:
        num_reads = fastq.count_sequences(fq_file)
    except Exception as e:
        print(e)
        return None
    description = "Reads after removal of sequencing library adapters"
    row = pd.DataFrame([[sample, "cutadapt", fq_file, num_reads,
                         description]], columns=HEADER)
    return row


def umi_tools_deplex_fq(tmp_dir):
    """
    Count number of reads in the FASTQ files output by
    :py:mod:`riboviz.tools.demultiplex_fastq`.

    ``tmp_dir`` is searched for directories matching
    :py:const:`riboviz.workflow_files.DEPLEX_DIR_FORMAT`.
    Each of these directories is traversed to identify FASTQ
    files. Each of these directories is also traversed to identify TSV
    files matching
    :py:const:`riboviz.demultiplex_fastq.NUM_READS_FILE`.

    If, for a directory, the TSV file exists it is parsed and the
    number of reads in each FASTQ file extracted. If the TSV file
    cannot be found then the number of reads in the FASTQ files
    themselves are counted.

    For each file a ``pandas.core.frame.Series`` is created with
    fields ``SampleName``, ``Program``, ``File``, ``NumReads``,
    ``Description``.

    :param tmp_dir: Directory
    :type tmp_dir: str or unicode
    :return: list of ``pandas.core.frame.Series``, or ``[]``
    :rtype: list(pandas.core.frame.Series)
    """
    deplex_dirs = glob.glob(os.path.join(
        tmp_dir, workflow_files.DEPLEX_DIR_FORMAT.format("*")))
    if not deplex_dirs:
        return []
    description = "Demultiplexed reads"
    rows = []
    for deplex_dir in deplex_dirs:
        fq_files = [glob.glob(os.path.join(deplex_dir, "*" + ext))
                    for ext in fastq.FASTQ_EXTS]
        # Flatten
        fq_files = [f for files in fq_files for f in files]
        if not fq_files:
            continue
        fq_files.sort()
        tsv_files = glob.glob(
            os.path.join(deplex_dir,
                         demultiplex_fastq.NUM_READS_FILE))
        is_tsv_problem = False
        if tsv_files:
            num_reads_file = tsv_files[0]
            print(num_reads_file)
            try:
                deplex_df = pd.read_csv(num_reads_file,
                                        delimiter="\t",
                                        comment="#")
                for fq_file in fq_files:
                    tag = os.path.basename(fq_file).split(".")[0]
                    tag_df = deplex_df[
                        deplex_df[sample_sheets.SAMPLE_ID] == tag]
                    num_reads = tag_df.iloc[0][sample_sheets.NUM_READS]
                    row = pd.DataFrame(
                        [[tag,
                          demultiplex_fastq_tools_module.__name__,
                          fq_file, num_reads, description]],
                        columns=HEADER)
                    rows.append(row)
            except Exception as e:
                print(e)
                is_tsv_problem = True
        if is_tsv_problem or not tsv_files:
            # Traverse FASTQ files directly.
            for fq_file in fq_files:
                print(fq_file)
                tag = os.path.basename(fq_file).split(".")[0]
                try:
                    num_reads = fastq.count_sequences(fq_file)
                except Exception as e:
                    print(e)
                    continue
                row = pd.DataFrame(
                    [[tag,
                      demultiplex_fastq_tools_module.__name__,
                      fq_file, num_reads, description]],
                    columns=HEADER)
                rows.append(row)
    return rows


def hisat2_fq(tmp_dir, sample, fq_file_name, description):
    """
    Count number of reads in the FASTQ file output by ``hisat2``.

    ``<tmp_dir>/<sample>`` is searched for a FASTQ file matching
    ``fq_file_name``. The number of reads in the file are counted.

    A ``pandas.core.frame.Series`` is created with fields
    ``SampleName``, ``Program``, ``File``, ``NumReads``,
    ``Description``.

    :param tmp_dir: Directory
    :type tmp_dir: str or unicode
    :param sample: Sample name
    :type sample: str or unicode
    :param fq_file_name: FASTQ file name pattern
    :type fq_file_name: str or unicode
    :param description: Description of this step
    :type description: str or unicode
    :return: ``pandas.core.frame.Series``, or ``None``
    :rtype: pandas.core.frame.Series
    """
    fq_files = glob.glob(os.path.join(tmp_dir, sample, fq_file_name))
    if not fq_files:
        return None
    fq_file = fq_files[0]  # Only 1 match expected
    print(fq_file)
    try:
        num_reads = fastq.count_sequences(fq_file)
    except Exception as e:
        print(e)
        return None
    row = pd.DataFrame([[sample, "hisat2", fq_file, num_reads,
                         description]], columns=HEADER)
    return row


def hisat2_sam(tmp_dir, sample, sam_file_name, description):
    """
    Count number of reads in the SAM file output by ``hisat2``.

    ``<tmp_dir>/<sample>`` is searched for a SAM file matching
    ``sam_file_name``. The number of reads in the file are counted.

    A ``pandas.core.frame.Series`` is created with fields
    ``SampleName``, ``Program``, ``File``, ``NumReads``,
    ``Description``.

    :param tmp_dir: Directory
    :type tmp_dir: str or unicode
    :param sample: Sample name
    :type sample: str or unicode
    :param sam_file_name: SAM file name pattern
    :type sam_file_name: str or unicode
    :param description: Description of this step
    :type description: str or unicode
    :return: ``pandas.core.frame.Series``, or ``None``
    :rtype: pandas.core.frame.Series
    """
    sam_files = glob.glob(os.path.join(tmp_dir, sample, sam_file_name))
    if not sam_files:
        return None
    sam_file = sam_files[0]  # Only 1 match expected.
    print(sam_file)
    try:
        sequences, _ = sam_bam.count_sequences(sam_file)
    except Exception as e:
        print(e)
        return None
    row = pd.DataFrame([[sample, "hisat2", sam_file, sequences,
                         description]], columns=HEADER)
    return row


def trim_5p_mismatch_sam(tmp_dir, sample):
    """
    Count number of reads in the SAM file output by
    :py:mod:`riboviz.tools.trim_5p_mismatch`.

    ``<tmp_dir>/<sample>`` is searched for a SAM file matching
    :py:const:`riboviz.workflow_files.ORF_MAP_CLEAN_SAM` and
    a TSV file matching
    :py:const:`riboviz.workflow_files.TRIM_5P_MISMATCH_TSV`.

    If the TSV file exists it is parsed and the number of reads output
    extracted. If the TSV file cannot be found then the number of
    reads in the SAM file itself are counted.

    A ``pandas.core.frame.Series`` is created with fields
    ``SampleName``, ``Program``, ``File``, ``NumReads``,
    ``Description``.

    :param tmp_dir: Directory
    :type tmp_dir: str or unicode
    :param sample: Sample name
    :type sample: str or unicode
    :return: ``pandas.core.frame.Series``, or ``None``
    :rtype: pandas.core.frame.Series
    """
    # Look for the SAM file.
    sam_files = glob.glob(os.path.join(
        tmp_dir, sample, workflow_files.ORF_MAP_CLEAN_SAM))
    if not sam_files:
        return None
    sam_file = sam_files[0]  # Only 1 match expected.
    # Look for trim_5p_mismatch.tsv.
    tsv_files = glob.glob(os.path.join(
        tmp_dir, sample, workflow_files.TRIM_5P_MISMATCH_TSV))
    is_tsv_problem = False
    if tsv_files:
        tsv_file = tsv_files[0]
        print(tsv_file)
        try:
            trim_data = pd.read_csv(tsv_file, delimiter="\t", comment="#")
            trim_row = trim_data.iloc[0]
            sequences = trim_row[trim_5p_mismatch.NUM_WRITTEN]
        except Exception as e:
            print(e)
            is_tsv_problem = True
    if is_tsv_problem or not tsv_files:
        # Traverse SAM file directly.
        print(sam_file)
        try:
            sequences, _ = sam_bam.count_sequences(sam_file)
        except Exception as e:
            print(e)
            return None
    description = "Reads after trimming of 5' mismatches and removal of those with more than 2 mismatches"
    row = pd.DataFrame([[sample,
                         trim_5p_mismatch_tools_module.__name__,
                         sam_file, sequences, description]],
                       columns=HEADER)
    return row


def umi_tools_dedup_bam(tmp_dir, output_dir, sample):
    """
    Count number of reads in the BAM file output by
    ``umi_tools dedup``.

    ``<tmp_dir>/<sample>`` is searched for a BAM file matching
    :py:const:`riboviz.workflow_files.DEDUP_BAM` and
    if this is found the reads in the output file
    ``<output_dir>/<sample>/<sample>.bam`` are counted.

    A ``pandas.core.frame.Series`` is created with fields
    ``SampleName``, ``Program``, ``File``, ``NumReads``,
    ``Description``.

    :param tmp_dir: Temporary directory
    :type tmp_dir: str or unicode
    :param output_dir: Output directory
    :type output_dir: str or unicode
    :param sample: Sample name
    :type sample: str or unicode
    :return: ``pandas.core.frame.Series``, or ``None``
    :rtype: pandas.core.frame.Series
    """
    # Look for dedup.bam.
    files = glob.glob(
        os.path.join(tmp_dir, sample, workflow_files.DEDUP_BAM))
    if not files:
        # Deduplication was not done.
        return None
    # Look for the BAM file output.
    files = glob.glob(os.path.join(
        output_dir, sample, sam_bam.BAM_FORMAT.format(sample)))
    if not files:
        return None
    file_name = files[0]
    print(file_name)
    try:
        sequences, _ = sam_bam.count_sequences(file_name)
    except Exception as e:
        print(e)
        return None
    description = "Deduplicated reads"
    row = pd.DataFrame(
        [[sample, "umi_tools dedup", file_name, sequences, description]],
        columns=HEADER)
    return row


def count_reads_df(config_file, input_dir, tmp_dir, output_dir):
    """
    Scan input, temporary and output directories and count the number
    of reads (sequences) processed by specific stages of a
    workflow. The scan is based on the directory structure and file
    patterns used by the workflow.

    A ``pandas.core.frame.DataFrame`` is created with columns
    ``SampleName``, ``Program``, ``File``, ``NumReads``,
    ``Description``.

    :param config_file: Configuration file
    :type config_file: str or unicode
    :param input_dir: Input files directory
    :type input_dir: str or unicode
    :param tmp_dir: Temporary files directory
    :type tmp_dir: str or unicode
    :param output_dir: Output files directory
    :type output_dir: str or unicode
    :return: ``pandas.core.frame.DataFrame``
    :rtype: pandas.core.frame.DataFrame
    """
    df = pd.DataFrame(columns=HEADER)
    # Create a multiprocessing pool.
    # We do not specify the worker number because this is the last
    # process of the workflow, so it can use all cores.
    pool = multiprocessing.Pool()
    result_ret = []
    # The item of result_ret is of form [type,content], where type
    # represents how the result should be processed.
    result_ret.append(['MULTIPLE',
                       input_fq(config_file, input_dir, pool)])
    result_ret.append(['APPEND',
                       pool.apply_async(cutadapt_fq, args=(tmp_dir,))])
    result_ret.append(['EXTEND',
                       pool.apply_async(umi_tools_deplex_fq, args=(tmp_dir,))])
    tmp_samples = [f.name for f in os.scandir(tmp_dir) if f.is_dir()]
    tmp_samples.sort()
    for sample in tmp_samples:
        result_ret.append(['APPEND',
                           pool.apply_async(cutadapt_fq,
                                            args=(tmp_dir, sample,))])
        result_ret.append(['APPEND',
                           pool.apply_async(hisat2_fq,
                                            args=(tmp_dir, sample, workflow_files.NON_RRNA_FQ,
                                                  "Reads that did not align to rRNA or other contaminating reads in rRNA index files",))])
        result_ret.append(['APPEND',
                           pool.apply_async(hisat2_sam,
                                            args=(tmp_dir, sample, workflow_files.RRNA_MAP_SAM,
                                                  "Reads aligned to rRNA and other contaminating reads in rRNA index files",))])
        result_ret.append(['APPEND',
                           pool.apply_async(hisat2_fq,
                                            args=(tmp_dir, sample, workflow_files.UNALIGNED_FQ,
                                                  "Unaligned reads removed by alignment of remaining reads to ORFs index files",))])
        result_ret.append(['APPEND',
                           pool.apply_async(hisat2_sam,
                                            args=(tmp_dir, sample, workflow_files.ORF_MAP_SAM,
                                                  "Reads aligned to ORFs index files",))])
        result_ret.append(['APPEND',
                           pool.apply_async(trim_5p_mismatch_sam,
                                            args=(tmp_dir, sample,))])
        result_ret.append(['APPEND',
                           pool.apply_async(umi_tools_dedup_bam,
                                            args=(tmp_dir, output_dir, sample,))])
        rows = []
    pool.close()
    pool.join()
    for i in result_ret:
        if i[0] == 'MULTIPLE':
            for j in i[1]:
                rows.append(j.get())
        elif i[0] == 'EXTEND':
            rows.extend(i[1].get())
        else:
            rows.append(i[1].get())
    rows = [row for row in rows if row is not None]
    df = df.append(rows)
    return df


def count_reads(config_file, input_dir, tmp_dir, output_dir, reads_file):
    """
    Scan input, temporary and output directories and count the number
    of reads (sequences) processed by specific stages of a
    workflow. The scan is based on the configuration, directory
    structure and file patterns used by the workflow.

    The output is written as tab-separated values into
    `reads_file`. The file header has column names ``SampleName``,
    ``Program``, ``File``, ``NumReads``, ``Description``.

    :param config_file: Configuration file
    :type config_file: str or unicode
    :param input_dir: Input files directory
    :type input_dir: str or unicode
    :param tmp_dir: Temporary files directory
    :type tmp_dir: str or unicode
    :param output_dir: Output files directory
    :type output_dir: str or unicode
    :param reads_file: Reads file output
    :type reads_file: str or unicode
    """
    reads_df = count_reads_df(config_file, input_dir, tmp_dir,
                              output_dir)
    provenance.write_provenance_header(__file__, reads_file)
    reads_df[list(reads_df.columns)].to_csv(
        reads_file, mode='a', sep="\t", index=False)


def equal_read_counts(file1, file2, comment="#"):
    """
    Compare two read counts files for equality. The comparison is done
    with the aid of Pandas DataFrames.

    The data frames are compared column-by-column. All columns, with
    the exception of ``File`` are compared for exact equality
    using ``pandas.core.series.Series.equals``. ``File`` is
    compared using the basename of the file only, ignoring
    the path.

    :param file1: File name
    :type file1: str or unicode
    :param file2: File name
    :type file2: str or unicode
    :param comment: Comment prefix
    :type comment: str or unicode
    :raise AssertionError: If files differ in their contents
    :raise Exception: If problems arise when loading the files
    """
    data1 = pd.read_csv(file1, sep="\t", comment=comment)
    data2 = pd.read_csv(file2, sep="\t", comment=comment)
    try:
        assert data1.shape == data2.shape,\
            "Unequal shape: %s, %s"\
            % (str(data1.shape), str(data2.shape))
        assert set(HEADER) == set(data1.columns),\
            "Unequal column names: %s, %s"\
            % (str(HEADER), str(data1.columns))
        assert set(data1.columns) == set(data2.columns),\
            "Unequal column names: %s, %s"\
            % (str(data1.columns), str(data2.columns))
        for column in [SAMPLE_NAME, PROGRAM, NUM_READS, DESCRIPTION]:
            column1 = data1[column]
            column2 = data2[column]
            assert column1.equals(column2),\
                "Unequal column values: %s" % column
        for (f1, f2) in zip(data1[FILE], data2[FILE]):
            f1_name = os.path.basename(f1)
            f2_name = os.path.basename(f2)
            assert f1_name == f2_name, \
                "Unequal column values: %s" % FILE
    except AssertionError as error:
        # Add file names to error message.
        message = error.args[0]
        message += " in file: " + str(file1) + ":" + str(file2)
        error.args = (message,)
        raise


if __name__ == '__main__':

    import sys
    a = sys.argv[1]
    b = sys.argv[2]
    equal_read_counts(a, b)
