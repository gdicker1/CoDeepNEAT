import gzip
import random
import math
import numpy as np
import time
import cPickle as pickle
from config import Config, load
import species
import chromosome
from chromosome import FFChromosome
from blu_chromosome import Blu_Chromosome
from mod_chromosome import Mod_Chromosome


class CDN_Population(object):
    """ Manages all the species  """
    evaluate = None  # Evaluates the entire population. You need to override
                     # this method in your experiments

    def __init__(self, popsize=1, gtype=FFChromosome, checkpoint_file=None):

        if checkpoint_file:
            # start from a previous point: creates an 'empty'
            # population and point its __dict__ to the previous one
            self.__resume_checkpoint(checkpoint_file)
        else:
            # total population size
            self.__popsize = popsize
            self.__population = []
            # currently living species
            self.__species = []
            # species history
            self.__species_log = []

            # Statistics
            self.__avg_fitness = []
            self.__best_fitness = []

            self._gtype = gtype
            self.__create_population()
            self.__generation = -1
            self.__elite = None

    stats = property(lambda self: (self.__best_fitness, self.__avg_fitness))
    species = property(lambda self: self.__species)
    species_log = property(lambda self: self.__species_log)
    population = property(lambda self: self.__population)
    elite = property(lambda self: self.__elite)
    gen = property(lambda self: self.__generation)

    def __resume_checkpoint(self, checkpoint):
        """ Resumes the simulation from a previous saved point. """
        try:
            # file = open(checkpoint)
            file = gzip.open(checkpoint)
        except IOError:
            raise
        print 'Resuming from a previous point: %s'%checkpoint
        # when unpickling __init__ is not called again
        previous_pop = pickle.load(file)
        self.__dict__ = previous_pop.__dict__

        print 'Loading random state'
        rstate = pickle.load(file)
        random.setstate(rstate)
        # random.jumpahead(1)
        file.close()

    def __create_checkpoint(self, report=False, save_dir=''):
        """ Saves the current simulation state. """
        # from time import strftime
        # get current time
        # date = strftime("%Y_%m_%d_%Hh%Mm%Ss")
        if report:
            print 'Creating checkpoint file at generation: %d' % self.__generation

        # dumps 'self'
        # file = open('checkpoint_'+str(self.__generation), 'w')
        file = gzip.open(save_dir + 'checkpoint_' + str(self.__generation), 'w', compresslevel=5)
        # dumps the population
        pickle.dump(self, file, protocol=2)
        # dumps the current random state
        pickle.dump(random.getstate(), file, protocol=2)
        file.close()

    def __create_population(self):
        if self._gtype == Blu_Chromosome:
            create = Blu_Chromosome.create_minimal_blueprint
        elif self._gtype == Mod_Chromosome:
            create = Mod_Chromosome.create_minimal_module
        else:
            create = FFChromosome.create_minimally_connected

        self.__population = []
        for i in xrange(self.__popsize):
            g = create()
            self.__population.append(g)

    def __repr__(self):
        s = "Population size: %d" %self.__popsize
        s += "\nTotal species: %d" %len(self.__species)
        return s

    def __len__(self):
        return len(self.__population)

    def __iter__(self):
        return iter(self.__population)

    def __getitem__(self, key):
        return self.__population[key]

    # def remove(self, chromo):
    #    ''' Removes a chromosome from the population '''
    #    self.__population.remove(chromo)

    def __speciate(self, report=False):
        """ Group chromosomes into species by similarity """
        if self._gtype == Blu_Chromosome:
            for indiv in self.__population:
                indiv.updateModPointers()
            
        # Speciate the population
        for individual in self:
            found = False
            for s in self.__species:
                if individual.distance(s.representant) < Config.compatibility_threshold:
                    s.add(individual)
                    found = True
                    break

            if not found:  # create a new species for this lone chromosome
                self.__species.append(species.Species(individual))
        # python technical note:
        # we need a "working copy" list when removing elements while looping
        # otherwise we might end up having sync issues
        for s in self.__species[:]:
            # this happens when no chromosomes are compatible with the species
            if len(s) == 0:
                if report:
                    print "Removing species %d for being empty" % s.id
                # remove empty species
                self.__species.remove(s)

        if self._gtype == Mod_Chromosome:
            Config.mod_species = [s.id for s in self.__species if len(s) > 0]

        self.__set_compatibility_threshold()

    def speciate(self, report=False):
        self.__speciate(report)

    def __set_compatibility_threshold(self):
        ''' Controls compatibility threshold '''
        if len(self.__species) > Config.species_size:
            Config.compatibility_threshold += Config.compatibility_change
        elif len(self.__species) < Config.species_size:
            if Config.compatibility_threshold > Config.compatibility_change:
                Config.compatibility_threshold -= Config.compatibility_change
            else:
                print 'Compatibility threshold cannot be changed (minimum value has been reached)'

    def average_fitness(self):
        """ Returns the average raw fitness of population """
        sum = 0.0
        for c in self:
            sum += c.fitness

        return sum / len(self)

    def stdeviation(self):
        """ Returns the population standard deviation """
        # first compute the average
        u = self.average_fitness()
        error = 0.0

        try:
            # now compute the distance from average
            for c in self:
                error += (u - c.fitness)**2
        except OverflowError:
            # TODO: catch OverflowError: (34, 'Numerical result out of range')
            print "Overflow - printing population status"
            print "error = %f \t average = %f" % (error, u)
            print "Population fitness:"
            print [c.fitness for c in self]

        return math.sqrt(error / len(self))

    def __compute_spawn_levels(self):
        """ Compute each species' spawn amount (Stanley, p. 40) """

        # 1. Boost if young and penalize if old
        # TODO: does it really increase the overall performance?
        species_stats = []
        for s in self.__species:
            if s.age < Config.youth_threshold:
                species_stats.append(s.average_fitness() * Config.youth_boost)
            elif s.age > Config.old_threshold:
                species_stats.append(s.average_fitness() * Config.old_penalty)
            else:
                species_stats.append(s.average_fitness())

        # 2. Share fitness (only usefull for computing spawn amounts)
        # More info: http://tech.groups.yahoo.com/group/neat/message/2203
        # Sharing the fitness is only meaningful here
        # we don't really have to change each individual's raw fitness
        total_average = 0.0
        for s in species_stats:
                total_average += s

        # 3. Compute spawn
        for i, s in enumerate(self.__species):
            if total_average > 0:
                s.spawn_amount = int(round((species_stats[i] * self.__popsize / total_average)))
            else:
                s.spawn_amount = int(round((species_stats[i] * self.__popsize))) + 1

    def __tournament_selection(self, k=2):
        """ Tournament selection with size k (default k=2).
            Make sure the population has at least k individuals """
        random.shuffle(self.__population)

        return max(self.__population[:k])

    def __log_species(self):
        """ Logging species data for visualizing speciation """
        if len(self.__species) < 1:
            print "Skipping logging for species, no species present"
            return
        max_species_id = max([s.id for s in self.__species])
        temp = []
        for i in xrange(1, max_species_id + 1):
            found_specie = False
            for s in self.__species:
                if i == s.id:
                    temp.append(len(s))
                    found_specie = True
                    break
            if not found_specie:
                temp.append(0)
        self.__species_log.append(temp)

    def _dump_species_log(self, fname="species_log"):
        n = np.array(self.__species_log)
        filename = fname + ".dat" if ".dat" not in fname else fname
        n.dump(fname)

    def __population_diversity(self):
        """ Calculates the diversity of population: total average weights,
            number of connections, nodes """

        num_nodes = 0
        num_conns = 0

        for c in self:
            num_nodes += len(c.node_genes)
            num_conns += len(c.conn_genes)

        total = len(self)
        return (num_nodes / total, num_conns / total)

    def step(self, report=True, checkpoint_generation=None, save_best=False, save_dir=''):
        ''' Do one epoch '''
        self.__generation += 1

        if report:
            print '\n ****** Running generation %d ****** \n' % self.__generation

        # Speciates the population
        self.__speciate(report)

        # Current generation's best chromosome
        self.__best_fitness.append(max(self.__population))
        # Current population's average fitness
        self.__avg_fitness.append(self.average_fitness())

        # Print some statistics
        best = self.__best_fitness[-1]
        self.__elite = best
        # Which species has the best chromosome?
        for s in self.__species:
            s.hasBest = False
            if best.species_id == s.id:
                s.hasBest = True

        # Stops the simulation
        if best.fitness > Config.max_fitness_threshold and self._gtype != Mod_Chromosome:
            print '\nBest individual found in epoch %s - complexity: %s' %(self.__generation, best.size())
            if save_best:
                file = open(save_dir + 'best_chromo_' + str(self.__generation), 'w')
                pickle.dump(best, file)
                file.close()
            return True

        # -----------------------------------------
        # Prints chromosome's parents id:  {dad_id, mom_id} -> child_id
        # for chromosome in self.__population:
        #    print '{%3d; %3d} -> %3d' %(chromosome.parent1_id, chromosome.parent2_id, chromosome.id)
        # -----------------------------------------

        # Remove stagnated species and its members (except if it has the best chromosome)
        for s in self.__species[:]:
            if s.no_improvement_age > Config.max_stagnation:
                if not s.hasBest:
                    if report:
                        print "\n   Species %2d (with %2d individuals) is stagnated: removing it" \
                                %(s.id, len(s))
                    # removing species
                    self.__species.remove(s)
                    # removing all the species' members
                    # TODO: can be optimized!
                    for c in self.__population[:]:
                        if c.species_id == s.id:
                            self.__population.remove(c)

        # Remove "super-stagnated" species (even if it has the best chromosome)
        # It is not clear if it really avoids local minima
        for s in self.__species[:]:
            if s.no_improvement_age > 2 * Config.max_stagnation:
                if report:
                    print "\n   Species %2d (with %2d individuals) is super-stagnated: removing it" \
                            %(s.id, len(s))
                # removing species
                self.__species.remove(s)
                # removing all the species' members
                # TODO: can be optimized!
                for c in self.__population[:]:
                    if c.species_id == s.id:
                        self.__population.remove(c)

        # Compute spawn levels for each remaining species
        self.__compute_spawn_levels()

        # Removing species with spawn amount = 0
        for s in self.__species[:]:
            # This rarely happens
            if s.spawn_amount == 0:
                if report:
                    print '   Species %2d age %2s removed: produced no offspring' %(s.id, s.age)
                for c in self.__population[:]:
                    if c.species_id == s.id:
                        self.__population.remove(c)
                            # self.remove(c)
                self.__species.remove(s)

        if len(self.__species) < 1:
            print "Ending on generation %d, no species left"%(self.__generation)
            return True
        # Logging speciation stats
        self.__log_species()

        if report:
            print 'Population size: %d \t Diversity: %s' %(len(self), self.__population_diversity())
            print 'Population\'s average fitness: %3.5f stdev: %3.5f' %(self.__avg_fitness[-1], self.stdeviation())
            print 'Best fitness: %2.12s - size: %s - species %s - id %s' \
                %(best.fitness, best.size(), best.species_id, best.id)
            # print some "debugging" information
            print 'Species length: %d totalizing %d individuals' \
                    %(len(self.__species), sum([len(s) for s in self.__species]))
            print 'Species ID       : %s' % [s.id for s in self.__species]
            print 'Each species size: %s' % [len(s) for s in self.__species]
            print 'Amount to spawn  : %s' % [s.spawn_amount for s in self.__species]
            print 'Species age      : %s' % [s.age for s in self.__species]
            print 'Species no improv: %s' % [s.no_improvement_age for s in self.__species] # species no improvement age

            #for s in self.__species:
            #    print s

        # -------------------------- Producing new offspring -------------------------- #
        new_population = []  # next generation's population

        # Spawning new population
        for s in self.__species:
            if report:
                print "   species %d producting %d new individuals" % (s.id, s.spawn_amount)
            new_population.extend(s.reproduce())

        # ----------------------------#
        # Controls under or overflow  #
        # ----------------------------#
        fill = (self.__popsize) - len(new_population)
        if fill < 0:  # overflow
            if report:
                print '   Removing %d excess individual(s) from the new population' %-fill
            # TODO: This is dangerous! I can't remove a species' representant!
            new_population = new_population[:fill]  # Removing the last added members

        if fill > 0:  # underflow
            if report:
                print '   Producing %d more individual(s) to fill up the new population' %fill

            # TODO:
            # what about producing new individuals instead of reproducing?
            # increasing diversity from time to time might help
            while fill > 0:
                # Selects a random chromosome from population
                parent1 = random.choice(self.__population)
                # Search for a mate within the same species
                found = False
                for c in self:
                    # what if c is parent1 itself?
                    if c.species_id == parent1.species_id:
                        child = parent1.crossover(c)
                        child.mutate()
                        new_population.append(child)
                        found = True
                        break
                if not found:
                    # If no mate was found, just mutate it
                    new_population.append(parent1.mutate())
                # new_population.append(chromosome.FFChromosome.create_fully_connected())
                fill -= 1

        assert self.__popsize == len(new_population), 'Different population sizes!'
        # Updates current population

        self.__population = new_population[:]

        if checkpoint_generation is not None and self.__generation % checkpoint_generation == 0:
            self.__create_checkpoint(report, save_dir)
            # saves the best chromo from the current generation
            if save_best:
                file = open(save_dir + 'best_chromo_' + str(self.__generation),'w')
                pickle.dump(best, file)
                file.close()

        self.__speciate(report)

        return False

    def epoch(self, n, report=True, save_best=False, checkpoint_interval=10,
              checkpoint_generation=None):
        """ Runs NEAT's genetic algorithm for n epochs.

            Keyword arguments:
            report -- show stats at each epoch (default True)
            save_best -- save the best chromosome from each epoch (default False)
            checkpoint_interval -- time in minutes between saving checkpoints (default 10 minutes)
            checkpoint_generation -- time in generations between saving checkpoints
                (default 0 -- option disabled)
        """
        t0 = time.time()  # for saving checkpoints

        for g in xrange(n):
            self.__generation += 1

            if report:
                print '\n ****** Running generation %d ****** \n' % self.__generation

            # Evaluate individuals
            self.evaluate()
            # Speciates the population
            self.__speciate(report)

            # Current generation's best chromosome
            self.__best_fitness.append(max(self.__population))
            # Current population's average fitness
            self.__avg_fitness.append(self.average_fitness())

            # Print some statistics
            best = self.__best_fitness[-1]
            # Which species has the best chromosome?
            for s in self.__species:
                s.hasBest = False
                if best.species_id == s.id:
                    s.hasBest = True

            # saves the best chromo from the current generation
            if save_best:
                file = open('best_chromo_' + str(self.__generation),'w')
                pickle.dump(best, file)
                file.close()

            # Stops the simulation
            if best.fitness > Config.max_fitness_threshold:
                print '\nBest individual found in epoch %s - complexity: %s' %(self.__generation, best.size())
                break

            # -----------------------------------------
            # Prints chromosome's parents id:  {dad_id, mom_id} -> child_id
            # for chromosome in self.__population:
            #    print '{%3d; %3d} -> %3d' %(chromosome.parent1_id, chromosome.parent2_id, chromosome.id)
            # -----------------------------------------

            # Remove stagnated species and its members (except if it has the best chromosome)
            for s in self.__species[:]:
                if s.no_improvement_age > Config.max_stagnation:
                    if not s.hasBest:
                        if report:
                            print "\n   Species %2d (with %2d individuals) is stagnated: removing it" \
                                    %(s.id, len(s))
                        # removing species
                        self.__species.remove(s)
                        # removing all the species' members
                        # TODO: can be optimized!
                        for c in self.__population[:]:
                            if c.species_id == s.id:
                                self.__population.remove(c)

            # Remove "super-stagnated" species (even if it has the best chromosome)
            # It is not clear if it really avoids local minima
            for s in self.__species[:]:
                if s.no_improvement_age > 2 * Config.max_stagnation:
                    if report:
                        print "\n   Species %2d (with %2d individuals) is super-stagnated: removing it" \
                                %(s.id, len(s))
                    # removing species
                    self.__species.remove(s)
                    # removing all the species' members
                    # TODO: can be optimized!
                    for c in self.__population[:]:
                        if c.species_id == s.id:
                            self.__population.remove(c)

            # Compute spawn levels for each remaining species
            self.__compute_spawn_levels()

            # Removing species with spawn amount = 0
            for s in self.__species[:]:
                # This rarely happens
                if s.spawn_amount == 0:
                    if report:
                        print '   Species %2d age %2s removed: produced no offspring' %(s.id, s.age)
                    for c in self.__population[:]:
                        if c.species_id == s.id:
                            self.__population.remove(c)
                                # self.remove(c)
                    self.__species.remove(s)

            if len(self.__species) < 1:
                print "Ending on epoch %d, no species left"%(g)
                return
            # Logging speciation stats
            self.__log_species()

            if report:
                print 'Poluation size: %d \t Diversity: %s' %(len(self), self.__population_diversity())
                #for indiv in self.__population:
                #    print str(indiv)
                print 'Population\'s average fitness: %3.5f stdev: %3.5f' %(self.__avg_fitness[-1], self.stdeviation())
                print 'Best fitness: %2.12s - size: %s - species %s - id %s' \
                    %(best.fitness, best.size(), best.species_id, best.id)
                # print some "debugging" information
                print 'Species length: %d totalizing %d individuals' \
                        %(len(self.__species), sum([len(s) for s in self.__species]))
                print 'Species ID       : %s' % [s.id for s in self.__species]
                print 'Each species size: %s' % [len(s) for s in self.__species]
                print 'Amount to spawn  : %s' % [s.spawn_amount for s in self.__species]
                print 'Species age      : %s' % [s.age for s in self.__species]
                print 'Species no improv: %s' % [s.no_improvement_age for s in self.__species] # species no improvement age

                #for s in self.__species:
                #    print s

            # -------------------------- Producing new offspring -------------------------- #
            new_population = []  # next generation's population

            # Spawning new population
            for s in self.__species:
                new_population.extend(s.reproduce())

            # ----------------------------#
            # Controls under or overflow  #
            # ----------------------------#
            fill = (self.__popsize) - len(new_population)
            if fill < 0:  # overflow
                if report:
                    print '   Removing %d excess individual(s) from the new population' %-fill
                # TODO: This is dangerous! I can't remove a species' representant!
                new_population = new_population[:fill]  # Removing the last added members

            if fill > 0:  # underflow
                if report:
                    print '   Producing %d more individual(s) to fill up the new population' %fill

                # TODO:
                # what about producing new individuals instead of reproducing?
                # increasing diversity from time to time might help
                while fill > 0:
                    # Selects a random chromosome from population
                    parent1 = random.choice(self.__population)
                    # Search for a mate within the same species
                    found = False
                    for c in self:
                        # what if c is parent1 itself?
                        if c.species_id == parent1.species_id:
                            child = parent1.crossover(c)
                            child.mutate()
                            new_population.append(child)
                            found = True
                            break
                    if not found:
                        # If no mate was found, just mutate it
                        new_population.append(parent1.mutate())
                    # new_population.append(chromosome.FFChromosome.create_fully_connected())
                    fill -= 1

            assert self.__popsize == len(new_population), 'Different population sizes!'
            # Updates current population
            self.__population = new_population[:]

            if checkpoint_interval is not None and time.time() > t0 + 60 * checkpoint_interval:
                self.__create_checkpoint(report)
                t0 = time.time()  # updates the counter
            elif checkpoint_generation is not None and self.__generation % checkpoint_generation == 0:
                self.__create_checkpoint(report)


if __name__ == '__main__':
    from visualize import plot_species
    print "Testing CoDeepNEAT population with excessive configuration parameters\n"

    # Necessary config values
    load('template_config')
    Config.input_nodes = [32, 32, 3]          # number of inputs
    Config.output_nodes = 10                  # number of outputs
    Config.modpopsize = 10
    Config.max_fitness_threshold = 2
    Config.prob_addmodule = 0.5
    Config.prob_addconn = 0.03
    Config.min_learnrate = 0.001
    Config.max_learnrate = 0.1
    Config.prob_mutatelearnrate = 1
    Config.learnrate_mutation_power = 0.002
    Config.min_size = 32
    Config.max_size = 256
    Config.size_mutation_power = 3
    Config.min_ksize = 3
    Config.max_ksize = 5
    Config.min_drop = 0.0
    Config.max_drop = 0.7
    Config.drop_mutation_power = 0.005
    Config.prob_addlayer = 0.5
    Config.prob_mutatelayersize = 0.5
    Config.prob_mutatekernel = 0.5
    Config.prob_mutatepadding = 0.5
    Config.prob_mutatedrop = 0.5
    Config.prob_mutatemaxpool = 0.5

    # sample fitness function
    def eval_fitness(population):
        for individual in population.population:
            individual.fitness = 1.0

    # set fitness function
    CDN_Population.evaluate = eval_fitness

    # creates the population
    b_pop = CDN_Population(10, Blu_Chromosome)
    m_pop = CDN_Population(15, Mod_Chromosome)
    #runs the simulation for 250 epochs
    print "-- Start Blueprint Test --"
    b_pop.epoch(100)
    print "-- End Blueprint Test --"
    print "-- Start Module Test --"
    m_pop.epoch(100)
    print "-- End Module Test --"
    plot_species(b_pop.species_log, "blu_speciation")
    plot_species(m_pop.species_log, "mod_speciation")
    print(m_pop.species_log)
    m_pop._dump_species_log("mod_species.dat")
    x = np.load("mod_species.dat")
    print m_pop.species_log == x

    spec = [s for s in m_pop.species]
    print [str(s) for s in spec]
    for s in spec:
        print [str(m) for m in s.members]
