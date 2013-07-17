import json
import re
import os
import shutil

from utils import md5_file, symlink, expand_tree, dot_concat
from make import check_dependency, check_three_way_dependency
from docs_meta import output_yaml, get_manual_path, get_conf, get_branch_output_path
from fabric.api import task, env, abort, puts, local
from multiprocessing import Pool, Process

env.input_file = None
env.output_file = None

@task
def input(fn):
    env.input_file = fn

@task
def output(fn):
    env.output_file = fn

########## Process Sphinx Json Output ##########

@task
def json_output():
    if env.input_file is None or env.output_file is None:
        all_json_output()
    else:
        process_json_file(env.input_file, env.output_file)

def all_json_output():
    p = Pool()
    conf = get_conf()

    outputs = []
    count = 0
    for fn in expand_tree('source', 'txt'):
        # path = build/<branch>/json/<filename>
        path = os.path.join(conf.build.paths.output, conf.git.branches.current,
                            'json', os.path.splitext(fn.split(os.path.sep, 1)[1])[0])
        fjson = dot_concat(path, 'fjson')
        json = dot_concat(path, 'json')

        if check_three_way_dependency(json, fn, fjson):
            p.apply_async(process_json_file, args=(fjson, json))
            count += 1

        outputs.append(json)

    p.apply_async(generate_list_file,
                  args=(outputs, os.path.join(get_branch_output_path(), 'json-file-list')))

    p.close()
    p.join()

    puts('[json]: processed {0} json files.'.format(str(count)))

def generate_list_file(outputs, path):
    dirname = os.path.dirname(path)

    if get_conf().git.remote.upstream.endswith('ecosystem'):
        url = 'http://docs.mongodb.org/ecosystem'
    else:
        url = '/'.join(['http://docs.mongodb.org', get_manual_path()])

    if not os.path.exists(dirname):
        os.mkdir(dirname)

    with open(path, 'w') as f:
        for fn in outputs:
            f.write( '/'.join([ url, 'json', fn.split('/', 3)[3:][0]]))
            f.write('\n')

    puts('[json]: rebuilt inventory of json output.')

def process_json_file(input_fn, output_fn):
    with open(input_fn, 'r') as f:
        document = f.read()

    doc = json.loads(document)

    if 'body' in doc:
        text = doc['body'].encode('ascii', 'ignore')

        text = re.sub(r'<a class=\"headerlink\"', '.<a', text)
        text = re.sub('<[^>]*>', '', text)
        text = re.sub('&#8220;', '"', text)
        text = re.sub('&#8221;', '"', text)
        text = re.sub('&#8216;', "'", text)
        text = re.sub('&#8217;', "'", text)
        text = re.sub(r'&#\d{4};', '', text)
        text = re.sub('&nbsp;', '', text)

        doc['text'] = ' '.join(text.split('\n')).strip()

    if 'url' in doc:
        url = [ 'http://docs.mongodb.org', get_manual_path() ]
        url.extend(input_fn.rsplit('.', 1)[0].split(os.path.sep)[3:])

        doc['url'] = '/'.join(url)

    if 'title' in doc:
        title = doc['title'].encode('ascii', 'ignore')
        title = re.sub('<[^>]*>', '', title)

        doc['title'] = title

    with open(output_fn, 'w') as f:
        f.write(json.dumps(doc))

    puts('[json]: generated a processed json file: ' + output_fn)

########## Update Dependencies ##########

def update_dependency(fn):
    if os.path.exists(fn):
        os.utime(fn, None)
        puts('[dependency]: updated timestamp of {0} because its included files changed'.format(fn))

def fix_include_path(inc, fn, source):
    if inc.startswith('/'):
        return ''.join([source + inc])
    else:
        return os.path.join(os.path.dirname(os.path.abspath(fn)), fn)

def check_deps(file, pattern):
    includes = []
    try:
        with open(file, 'r') as f:
            for line in f:
                r = pattern.findall(line)
                if r:
                    includes.append(fix_include_path(r[0], file, 'source'))
        if len(includes) >= 1:
            if check_dependency(file, includes):
                update_dependency(file)
    except IOError:
        pass

@task
def refresh_dependencies():
    files = expand_tree('source', 'txt')
    inc_pattern = re.compile(r'\.\. include:: (.*\.(?:txt|rst))')

    dep_info = []
    p = Pool()

    for fn in files:
        p.apply_async(check_deps, kwds=dict(file=fn, pattern=inc_pattern))

    p.close()
    p.join()


########## Simple Tasks ##########

@task
def meta():
    output_yaml(env.output_file)


@task
def touch(fn, times=None):
    if os.path.exists(fn):
        os.utime(fn, times)

########## Main Output Processing Targets ##########

@task
def manpage_url():
    if env.input_file is None:
        abort('[man]: you must specify input and output files.')

    project_source = 'source'

    top_level_items = set()
    for fs_obj in os.listdir(project_source):
        if fs_obj.startswith('.static') or fs_obj == 'index.txt':
            continue
        if os.path.isdir(os.path.join(project_source, fs_obj)):
            top_level_items.add(fs_obj)
        if fs_obj.endswith('.txt'):
            top_level_items.add(fs_obj[:-4])

    top_level_items = '/' + '.*|/'.join(top_level_items)
    re_string = '(\\\\fB({0}.*)\\\\fP)'.format(top_level_items)

    with open(env.input_file, 'r') as f:
        manpage = f.read()

    manpage = re.sub(re_string, "http://docs.mongodb.org/manual\\2", manpage)

    with open(env.input_file, 'w') as f:
        f.write(manpage)

    puts("[{0}]: fixed urls in {1}".format('man', env.input_file))

@task
def copy_if_needed(builder='build'):
    if os.path.isfile(env.input_file) is False:
        abort("[{0}]: Input file does not exist.".format(builder))
    elif os.path.isfile(env.output_file) is False:
        if not os.path.exists(os.path.dirname(env.output_file)):
            os.makedirs(os.path.dirname(env.output_file))
        shutil.copyfile(env.input_file, env.output_file)
        puts('[{0}]: created "{1}" which did not exist.'.format(builder, env.input_file))
    else:
        if md5_file(env.input_file) == md5_file(env.output_file):
            puts('[{0}]: "{1}" not changed.'.format(builder, env.input_file))
        else:
            shutil.copyfile(env.input_file, env.output_file)
            puts('[{0}]: "{1}" changed.'.format(builder, env.input_file))

@task
def create_link():
    out_dirname = os.path.dirname(env.input_file)
    if out_dirname != '' and not os.path.exists(out_dirname):
        os.makedirs(out_dirname)

    if os.path.islink(env.output_file):
        os.remove(env.output_file)
    elif os.path.isdir(env.output_file):
        abort('[{0}]: {1} exists and is a directory'.format('link', env.output_file))
    elif os.path.exists(env.output_file):
        abort('[{0}]: could not create a symlink at {1}.'.format('link', env.output_file))

    symlink(env.output_file, env.input_file)
    puts('[{0}] created symbolic link pointing to "{1}" named "{2}"'.format('symlink', env.input_file, env.output_file))

@task
def manual_single_html():
    if env.input_file is None or env.output_file is None:
        abort('[single]: you must specify input and output files.')

    with open(env.input_file, 'r') as f:
        text = f.read()

    text = re.sub('href="contents.html', 'href="index.html', text)
    text = re.sub('name="robots" content="index"', 'name="robots" content="noindex"', text)
    text = re.sub('(href=")genindex.html', '\1../genindex/', text)

    with open(env.output_file, 'w') as f:
        f.write(text)
