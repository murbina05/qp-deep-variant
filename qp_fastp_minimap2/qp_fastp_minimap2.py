# -----------------------------------------------------------------------------
# Copyright (c) 2020, Qiita development team.
#
# Distributed under the terms of the BSD 3-clause License License.
#
# The full license is in the file LICENSE, distributed with this software.
# -----------------------------------------------------------------------------
import pandas as pd

from os import environ
from os.path import basename, join
from glob import glob
from itertools import zip_longest

from qiita_client import ArtifactInfo

MEMORY = '16g'
WALLTIME = '30:00:00'
FINISH_MEMORY = '10g'
FINISH_WALLTIME = '10:00:00'
MAX_RUNNING = 8

QC_REFERENCE_DB = environ["QC_REFERENCE_DB"]

FASTP_BASE = 'fastp -l 100 -i %s -w {nprocs} '
MINIMAP2_BASE = 'minimap2 -ax sr -t {nprocs} {database} - -a '
IVAR_TRIM_BASE = 'ivar trim -x {nprocs} -e -b {primer} -i %s'
# SAMTOOLS_BASE = 'samtools fastq -@ {nprocs} -f '

IVAR_TRIM_CMD = ' '.join([IVAR_TRIM_BASE, '-p {out_dir}/%s'])
FASTP_CMD = ' '.join([FASTP_BASE, '-I %s -o {out_dir}/%s -O {out_dir}/%s'])
FASTP_CMD_SINGLE = (f'{FASTP_BASE} -o '
                    '{out_dir}/%s')
COMBINED_CMD = (f'{FASTP_BASE} -I %s --stdout | {MINIMAP2_BASE} | '
                '{out_dir}/%s -2 {out_dir}/%s')
COMBINED_CMD_SINGLE = (f'{FASTP_BASE} --stdout | {MINIMAP2_BASE} | '
                       '{out_dir}/%s')


def get_dbs_list():
    folder = QC_REFERENCE_DB

    # skip human database
    return [basename(f) for f in glob(f'{folder}/*.bed')]


def _generate_commands(bam_file, primer, nprocs, out_dir):
    """Helper function to generate commands and facilite testing"""
    files = bam_file
    cmd = IVAR_TRIM_CMD
    command = cmd.format(nprocs=nprocs, primer=primer, out_dir=out_dir)

    out_files = []
    commands = []
    for bam in bam_file:
        fname = basename(bam)
        out_files.append((f'{out_dir}/{fname}', 'trimmed'))
        cmd = command % (bam, fname)
        commands.append(cmd)

    return commands, out_files


def fastp_minimap2(qclient, job_id, parameters, out_dir):
    """Run fastp and minimap2 with the given parameters

    Parameters
    ----------
    qclient : tgp.qiita_client.QiitaClient
        The Qiita server client
    job_id : str
        The job id
    parameters : dict
        The parameter values to run split libraries
    out_dir : str
        The path to the job's output directory

    Returns
    -------
    bool, list, str
        The results of the job
    """

    qclient.update_job_step(
        job_id, "Step 3 of 4: Finishing fastp and minimap2")

    ainfo = []
    # Generates 2 artifacts: one for the ribosomal
    # reads and other for the non-ribosomal reads
    out_files = []
    with open(f'{out_dir}/{job_id}.out_files.tsv') as f:
        for line in f.readlines():
            fp, ft = line.split()
            out_files.append((fp, ft))

    # Step 4 generating artifacts
    msg = "Step 4 of 4: Generating new artifact"
    qclient.update_job_step(job_id, msg)
    ainfo = [ArtifactInfo('Filtered files', 'per_sample_FASTQ', out_files)]

    return True, ainfo, ""


def fastp_minimap2_to_array(files, out_dir, params, prep_info, url, job_id):
    """Creates qsub files for submission of per sample fastp and minimap2

    Parameters
    ----------
    files : dict
        The dictionary of files to process, raw_forward_seqs/raw_reverse_seqs
    out_dir : str
        The output directory
    params : dict
        The parameter values to run fastp/minimap2
    prep_info : str
        The path to prep_info
    url : str
        The url to send info to
    job_id : str
        The job id

    Returns
    -------
    str, str, str
        The paths of the main_qsub_fp, finish_qsub_fp, out_files_fp
    """
    database = None
    if params['reference'] != 'None':
        database = [join(QC_REFERENCE_DB, f'{db}')
                    for db in get_dbs_list()
                    if params['reference'] in db][0]

    fwd_seqs = sorted(files['raw_forward_seqs'])
    if 'raw_reverse_seqs' in files:
        rev_seqs = sorted(files['raw_reverse_seqs'])
    else:
        rev_seqs = []

    df = pd.read_csv(prep_info, sep='\t', dtype='str',
                     na_values=[], keep_default_na=True)
    df.set_index('sample_name', inplace=True)
    if 'run_prefix' not in df.columns:
        raise ValueError('Missing run_prefix column in your preparation')

    # Note that for processing we don't actually need the run_prefix so
    # we are not going to use it and simply loop over the ordered
    # fwd_seqs/rev_seqs
    commands, out_files = _generate_commands(
        fwd_seqs, rev_seqs, database, params['threads'], out_dir)

    # writing the job array details
    details_name = join(out_dir, 'fastp_minimap2.array-details')
    with open(details_name, 'w') as details:
        details.write('\n'.join(commands))
    n_jobs = len(commands)

    # all the setup pieces
    PPN = params['threads']
    lines = ['#!/bin/bash',
             '#PBS -M qiita.help@gmail.com',
             f'#PBS -N {job_id}',
             f'#PBS -l nodes=1:ppn={PPN}',
             f'#PBS -l walltime={WALLTIME}',
             f'#PBS -l mem={MEMORY}',
             f'#PBS -o {out_dir}/{job_id}' + '_${PBS_ARRAYID}.log',
             f'#PBS -e {out_dir}/{job_id}' + '_${PBS_ARRAYID}.err',
             f'#PBS -t 1-{n_jobs}%{MAX_RUNNING}',
             '#PBS -l epilogue=/home/qiita/qiita-epilogue.sh',
             'set -e',
             f'cd {out_dir}',
             f'{params["environment"]}',
             'date',  # start time
             'hostname',  # executing system
             'echo ${PBS_JOBID} ${PBS_ARRAYID}',
             'offset=${PBS_ARRAYID}',
             'step=$(( $offset - 0 ))',
             f'cmd=$(head -n $step {details_name} | tail -n 1)',
             'eval $cmd',
             'set +e',
             'date']
    main_qsub_fp = join(out_dir, f'{job_id}.qsub')
    with open(main_qsub_fp, 'w') as job:
        job.write('\n'.join(lines))
        job.write('\n')

    # finish job
    lines = ['#!/bin/bash',
             '#PBS -M qiita.help@gmail.com',
             f'#PBS -N finish-{job_id}',
             '#PBS -l nodes=1:ppn=1',
             f'#PBS -l walltime={FINISH_WALLTIME}',
             f'#PBS -l mem={FINISH_MEMORY}',
             f'#PBS -o {out_dir}/finish-{job_id}.log',
             f'#PBS -e {out_dir}/finish-{job_id}.err',
             '#PBS -l epilogue=/home/qiita/qiita-epilogue.sh',
             'set -e',
             f'cd {out_dir}',
             f'{params["environment"]}',
             'date',  # start time
             'hostname',  # executing system
             'echo $PBS_JOBID',
             f'finish_qp_fastp_minimap2 {url} {job_id} {out_dir}\n'
             "date"]
    finish_qsub_fp = join(out_dir, f'{job_id}.finish.qsub')
    with open(finish_qsub_fp, 'w') as out:
        out.write('\n'.join(lines))
        out.write('\n')

    out_files_fp = join(out_dir, f'{job_id}.out_files.tsv')
    with open(out_files_fp, 'w') as out:
        out.write('\n'.join([f'{fp}\t{ft}'for fp, ft in out_files]))

    return main_qsub_fp, finish_qsub_fp, out_files_fp
